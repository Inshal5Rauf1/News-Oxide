import json, os, shutil, subprocess, sys, threading

import curate

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "day")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATE = "2026-10-29"


def _items(n, sources=None):
    return [dict(id="i%d" % k, sources=list((sources or {}).get(k, ["hackernews"])),
                 title="Title %d" % k, url="https://e.com/%d" % k, meta="m", hn_url=None,
                 desc="d")
            for k in range(n)]


def _batch(prompt):
    """The JSON payload a curate pass appended to its prompt file."""
    return json.loads(prompt.rsplit("\n\n", 1)[1])


def _day_dir(tmp_path):
    for k in curate.KEYS:
        shutil.copy(os.path.join(FIX, k + ".json"), tmp_path / ("%s-%s.json" % (DATE, k)))
    return str(tmp_path)


def test_gists_batches_and_fills_missing_with_title():
    items = _items(5)
    calls, lock = [], threading.Lock()

    def ask(model, prompt, timeout, schema=None):
        batch = _batch(prompt)
        with lock:
            calls.append([b["id"] for b in batch])
        assert model == "haiku" and schema == curate.GIST_SCHEMA and timeout == 120
        assert set(batch[0]) == {"id", "title", "url", "meta", "desc", "sources"}
        out = [dict(id=b["id"], gist="  Gist of %s.  " % b["id"], tags=["LLM", 3, "a", "b", "c"])
               for b in batch if b["id"] != "i3"]
        return {"items": out}

    out = curate.gists(items, ask, batch_size=2)
    assert sorted(calls) == [["i0", "i1"], ["i2", "i3"], ["i4"]]
    assert [o["id"] for o in out] == ["i0", "i1", "i2", "i3", "i4"]
    assert out[0]["gist"] == "Gist of i0." and out[0]["tags"] == ["llm", "a", "b"]
    assert out[3]["gist"] == "Title 3" and out[3]["tags"] == []
    assert out[0]["title"] == "Title 0" and "gist" not in items[0]      # copies, input untouched


def test_gists_truncates_140():
    long = "x" * 200

    def ask(model, prompt, timeout, schema=None):
        return {"items": [dict(id=b["id"], gist=long, tags=["t"]) for b in _batch(prompt)]}

    g = curate.gists(_items(1), ask)[0]["gist"]
    assert len(g) == 140 and g == "x" * 137 + "..."
    ok = curate.gists(_items(1), lambda m, p, t, schema=None: {
        "items": [dict(id="i0", gist="y" * 140, tags=[])]})[0]["gist"]
    assert ok == "y" * 140


def test_gists_failed_batch_falls_back_after_retry():
    calls, lock = {}, threading.Lock()

    def ask(model, prompt, timeout, schema=None):
        batch = _batch(prompt)
        first = batch[0]["id"]
        with lock:
            calls[first] = calls.get(first, 0) + 1
        if first == "i0":
            return None
        if first == "i2" and calls[first] == 1:
            return {"wrong": 1}                         # malformed once, then fine
        return {"items": [dict(id=b["id"], gist="G " + b["id"], tags=["t"]) for b in batch]}

    out = curate.gists(_items(5), ask, batch_size=2)
    assert calls == {"i0": 2, "i2": 2, "i4": 1}
    assert [o["gist"] for o in out] == ["Title 0", "Title 1", "G i2", "G i3", "G i4"]
    assert out[0]["tags"] == []


def test_themes_failure_returns_ungrouped():
    items = [dict(it, gist="g", tags=["t"]) for it in _items(3)]
    calls = []

    def ask(model, prompt, timeout, schema=None):
        calls.append(model)
        assert schema == curate.THEMES_SCHEMA and timeout == 300
        assert _batch(prompt) == [dict(id=i["id"], gist="g", tags=["t"], sources=["hackernews"])
                                  for i in items]
        return None

    r = curate.themes(items, ask)
    assert calls == ["sonnet", "sonnet"]
    assert r == {"themes": [], "rest": ["i0", "i1", "i2"], "grouped": False}


def test_themes_success_passes_raw_result():
    items = [dict(it, gist="g", tags=[]) for it in _items(2)]
    raw = {"themes": [{"title": "T", "summary": "S", "item_ids": ["i0", "i1", "zz"]}], "rest": []}
    r = curate.themes(items, lambda m, p, t, schema=None: raw)
    assert r == dict(raw, grouped=True)


def test_enforce_invariant_repairs_model_output():
    items = _items(6)
    result = {"grouped": True, "rest": ["i4", "i0", "ghost2"], "themes": [
        {"title": "  One ", "summary": 5, "item_ids": ["i0", "i1", "ghost", "i1"]},
        {"title": "Two", "summary": "s", "item_ids": ["i1", "i2"]},     # i1 dup -> 1 item left
        {"title": "", "summary": "s", "item_ids": ["i3"]},              # 1-item theme
    ]}
    r = curate.enforce(items, result)
    assert r["themes"] == [{"title": "One", "summary": "5", "item_ids": ["i0", "i1"]}]
    assert r["rest"] == ["i4", "i2", "i3", "i5"]
    placed = [i for t in r["themes"] for i in t["item_ids"]] + r["rest"]
    assert sorted(placed) == sorted(i["id"] for i in items) and len(placed) == len(items)
    assert r["grouped"] is True


def test_enforce_tolerates_garbage_and_ungroups_when_no_theme_survives():
    items = _items(3)
    for bad in (None, "x", {"themes": "no", "rest": 7}, {"themes": [None, {"item_ids": "i0"}]},
                {"themes": [{"title": "T", "item_ids": ["i0"]}], "rest": [], "grouped": True}):
        r = curate.enforce(items, bad)
        assert r == {"themes": [], "rest": ["i0", "i1", "i2"], "grouped": False}
    r = curate.enforce(items, {"themes": [{"item_ids": ["i2", "i0"]}], "rest": ["i1"],
                               "grouped": True})
    assert r["themes"] == [{"title": "Untitled", "summary": "", "item_ids": ["i2", "i0"]}]


def test_enforce_orders_by_sources_then_count():
    items = _items(10, sources={0: ["hackernews"], 1: ["feeds"], 2: ["reddit"],
                               3: ["trending", "newrepos"], 4: ["trending"]})
    t = lambda name, ids: {"title": name, "summary": "", "item_ids": ids}
    result = {"grouped": True, "rest": [], "themes": [
        t("A one source two items", ["i5", "i6"]),
        t("B two sources", ["i3", "i4"]),
        t("C three sources", ["i0", "i1", "i2"]),
        t("D one source three items", ["i7", "i8", "i9", "i4"]),  # i4 dup: 3 items left
        t("E one source two items", ["i8", "i4"]),               # both dups: dissolved, empty
    ]}
    r = curate.enforce(items, result)
    assert [x["title"] for x in r["themes"]] == [
        "C three sources", "B two sources", "D one source three items", "A one source two items"]
    assert r["rest"] == []


def test_run_probe_false_skips_model_and_still_writes(tmp_path):
    sent = _day_dir(tmp_path)

    def ask(*a, **k):
        raise AssertionError("ask must not be called when the probe fails")

    out = curate.run(DATE, sent, str(tmp_path / "tmp"), ask=ask, probe=lambda: False)
    with open(os.path.join(sent, DATE + "-curated.json")) as f:
        assert json.load(f) == out
    assert out["stats"] == {"items_in": 36, "items_placed": 36, "sources": curate.KEYS,
                            "grouped": False, "probe_ok": False}
    assert out["themes"] == [] and len(out["rest"]) == 36
    assert all(i["gist"] == i["title"] and i["tags"] == [] for i in out["items"].values())
    assert set(out["rest"]) == set(out["items"])


def test_run_no_model_does_not_probe(tmp_path):
    sent = _day_dir(tmp_path)

    def probe():
        raise AssertionError("probe must not run with no_model")

    out = curate.run(DATE, sent, str(tmp_path / "tmp"), ask=None, probe=probe, no_model=True)
    assert out["stats"]["probe_ok"] is None and out["stats"]["grouped"] is False


def test_run_writes_curated_json_counts_match(tmp_path):
    sent = _day_dir(tmp_path)
    os.remove(os.path.join(sent, DATE + "-reddit.json"))
    models = []

    def ask(model, prompt, timeout, schema=None):
        models.append(model)
        batch = _batch(prompt)
        if schema is curate.GIST_SCHEMA:
            return {"items": [dict(id=b["id"], gist="About " + b["id"], tags=["tech"])
                              for b in batch]}
        ids = [b["id"] for b in batch]
        return {"themes": [{"title": "First four", "summary": "Same.", "item_ids": ids[:4]},
                           {"title": "Ghosts", "summary": "None.", "item_ids": ["nope"]}],
                "rest": ids[4:-1]}                                  # forgets the last one

    out = curate.run(DATE, sent, str(tmp_path / "tmp"), ask=ask, probe=lambda: True)
    with open(os.path.join(sent, DATE + "-curated.json")) as f:
        assert json.load(f) == out
    n = len(out["items"])
    assert models.count("sonnet") == 1 and models.count("haiku") == 1
    assert out["date"] == DATE and os.path.isdir(tmp_path / "tmp")
    assert out["stats"] == {"items_in": n, "items_placed": n,
                            "sources": [k for k in curate.KEYS if k != "reddit"],
                            "grouped": True, "probe_ok": True}
    assert [t["title"] for t in out["themes"]] == ["First four"]
    assert len(out["themes"][0]["item_ids"]) + len(out["rest"]) == n
    assert all(i["gist"] == "About " + iid for iid, i in out["items"].items())


def test_cli_no_model_and_usage(tmp_path):
    sent = _day_dir(tmp_path)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "curate.py"), DATE, sent,
                        str(tmp_path / "t"), "--no-model"], capture_output=True, text=True)
    assert p.returncode == 0
    assert p.stdout == "curate: 36 items, 0 themes, grouped=0, probe=skipped\n"
    assert os.path.exists(os.path.join(sent, DATE + "-curated.json"))
    p = subprocess.run([sys.executable, os.path.join(ROOT, "curate.py"), DATE],
                       capture_output=True, text=True)
    assert p.returncode == 1 and "usage" in p.stderr


def test_gists_raising_ask_falls_back_for_that_batch_only(capsys):
    def ask(model, prompt, timeout, schema=None):
        batch = _batch(prompt)
        if batch[0]["id"] == "i2":
            raise RuntimeError("boom")
        return {"items": [dict(id=b["id"], gist="G " + b["id"], tags=[]) for b in batch]}

    out = curate.gists(_items(5), ask, batch_size=2)
    assert [o["gist"] for o in out] == ["G i0", "G i1", "Title 2", "Title 3", "G i4"]
    assert capsys.readouterr().err.count("curate: gists raised: boom") == 2


def test_gists_empty_items_for_nonempty_batch_retried_then_titles():
    calls, lock = {}, threading.Lock()

    def ask(model, prompt, timeout, schema=None):
        batch = _batch(prompt)
        with lock:
            calls[batch[0]["id"]] = calls.get(batch[0]["id"], 0) + 1
        if batch[0]["id"] == "i0":
            return {"items": []}
        return {"items": [dict(id=b["id"], gist="G " + b["id"], tags=[]) for b in batch]}

    out = curate.gists(_items(3), ask, batch_size=2)
    assert calls == {"i0": 2, "i2": 1}
    assert [(o["id"], o["gist"]) for o in out] == [("i0", "Title 0"), ("i1", "Title 1"),
                                                   ("i2", "G i2")]


def test_run_raising_probe_skips_model_and_still_writes(tmp_path, capsys):
    sent = _day_dir(tmp_path)

    def probe():
        raise OSError("no cli")

    def ask(*a, **k):
        raise AssertionError("ask must not be called")

    out = curate.run(DATE, sent, str(tmp_path / "tmp"), ask=ask, probe=probe)
    assert os.path.exists(os.path.join(sent, DATE + "-curated.json"))
    assert out["stats"]["probe_ok"] is False and out["stats"]["grouped"] is False
    assert len(out["rest"]) == out["stats"]["items_in"] == 36
    assert "curate: probe raised: no cli" in capsys.readouterr().err


def test_main_exits_2_when_placement_repeats_an_id(tmp_path, monkeypatch, capsys):
    sent = _day_dir(tmp_path)

    def bad_enforce(items, result):
        ids = [it["id"] for it in items]
        return {"themes": [], "rest": ids[:-1] + ids[:1], "grouped": False}   # same count

    monkeypatch.setattr(curate, "enforce", bad_enforce)
    assert curate.main([DATE, sent, str(tmp_path / "t"), "--no-model"]) == 2
    assert os.path.exists(os.path.join(sent, DATE + "-curated.json"))
    assert "invariant broken: 36 in, 36 placed" in capsys.readouterr().err


def test_normalise_desc_per_source():
    day = {"trending": {"daily": [dict(full_name="a/b", url="https://github.com/a/b",
                                       description="A tool")]},
           "feeds": [dict(title="Post", url="https://github.com/a/b", summary="x" * 400),
                     dict(title="Post 2", url="https://blog.example/2", summary="y" * 400)],
           "huggingface": {"models": [dict(id="m", url="https://hf.co/m", pipeline_tag="text-gen")],
                           "spaces": [dict(id="s", url="https://hf.co/s", sdk="gradio")],
                           "papers": [dict(title="P", url="https://hf.co/p", authors="Ann, Bo")]},
           "hackernews": [dict(title="HN", url="https://news.example/1", hn_url="https://hn/1")]}
    got = {i["title"]: i["desc"] for i in curate.normalise(day)}
    assert got == {"a/b": "A tool", "Post 2": "y" * 300, "m": "text-gen", "s": "gradio",
                   "P": "Ann, Bo", "HN": ""}                     # merged a/b keeps first desc

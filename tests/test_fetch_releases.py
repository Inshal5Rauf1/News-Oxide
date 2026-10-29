import json
from datetime import datetime, timedelta, timezone

import fetch_sources


def _atom(repo, entries):
    body = ""
    for tag, when in entries:
        pub = f"<updated>{when.strftime('%Y-%m-%dT%H:%M:%SZ')}</updated>" if when else ""
        body += (f"<entry><id>tag:github.com,2008:Repository/1/{tag}</id>"
                 f'<link rel="alternate" href="https://github.com/{repo}/releases/tag/{tag}"/>'
                 f"<title>Release {tag}</title>{pub}<content>notes {tag}</content></entry>")
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>r</title>{body}</feed>'


def _setup(monkeypatch, tmp_path, pages, listed, seen=None, starred=None):
    """pages: {repo: atom text or Exception}; starred: list of repos, or Exception."""
    lst = tmp_path / "releases.txt"
    lst.write_text("# c\n\n" + "\n".join(listed) + "\n")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    if seen is not None:
        (state / "releases.json").write_text(json.dumps(seen))
    monkeypatch.setattr(fetch_sources, "RELEASES_TXT", str(lst))
    monkeypatch.setattr(fetch_sources, "STATE_DIR", str(state))
    monkeypatch.setattr(fetch_sources, "TMP_DIR", str(tmp_path / "tmp"))

    def fake_get(url, headers=None):
        if "api.github.com" in url:
            if isinstance(starred, Exception):
                raise starred
            if "page=1" in url:
                return json.dumps([{"full_name": r} for r in (starred or [])])
            return "[]"
        repo = url.split("github.com/")[1].replace("/releases.atom", "")
        page = pages[repo]
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(fetch_sources, "get", fake_get)


def _state(tmp_path):
    return json.loads((tmp_path / "tmp" / "releases.state.json").read_text())


def test_releases_first_run_seeds_and_prints_empty(monkeypatch, tmp_path, capsys):
    now = datetime.now(timezone.utc)
    _setup(monkeypatch, tmp_path, {"a/a": _atom("a/a", [("v1", now), ("v0", now - timedelta(days=1))])}, ["a/a"])
    fetch_sources.releases()
    assert json.loads(capsys.readouterr().out) == []
    assert _state(tmp_path) == {"a/a": ["tag:github.com,2008:Repository/1/v1", "tag:github.com,2008:Repository/1/v0"]}


def test_releases_new_entry_within_7_days(monkeypatch, tmp_path, capsys):
    now = datetime.now(timezone.utc)
    page = _atom("a/a", [("v2", now - timedelta(days=1)), ("v1", now - timedelta(days=2))])
    _setup(monkeypatch, tmp_path, {"a/a": page}, ["a/a"],
           seen={"a/a": ["tag:github.com,2008:Repository/1/v1"]})
    fetch_sources.releases()
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    r = out[0]
    assert (r["repo"], r["tag"], r["name"], r["notes"]) == ("a/a", "v2", "Release v2", "notes v2")
    assert r["url"] == "https://github.com/a/a/releases/tag/v2" and r["published"]
    assert len(_state(tmp_path)["a/a"]) == 2


def test_releases_old_entry_ignored_even_if_unseen(monkeypatch, tmp_path, capsys):
    now = datetime.now(timezone.utc)
    page = _atom("a/a", [("old", now - timedelta(days=30)), ("undated", None)])
    _setup(monkeypatch, tmp_path, {"a/a": page}, ["a/a"], seen={"a/a": []})
    fetch_sources.releases()
    assert json.loads(capsys.readouterr().out) == []


def test_releases_starred_merge_and_failure_tolerated(monkeypatch, tmp_path, capsys):
    now = datetime.now(timezone.utc)
    pages = {"a/a": _atom("a/a", [("v1", now)]), "s/s": _atom("s/s", [("v1", now)]),
             "bad/bad": RuntimeError("boom")}
    _setup(monkeypatch, tmp_path, pages, ["a/a", "bad/bad"], seen={}, starred=["s/s", "a/a"])
    fetch_sources.releases()
    cap = capsys.readouterr()
    assert {r["repo"] for r in json.loads(cap.out)} == {"a/a", "s/s"}
    assert "releases: bad/bad failed: boom" in cap.err
    assert set(_state(tmp_path)) == {"a/a", "s/s"}

    _setup(monkeypatch, tmp_path, pages, ["a/a"], seen={}, starred=RuntimeError("rate limited"))
    fetch_sources.releases()
    cap = capsys.readouterr()
    assert "releases: starred fetch failed: rate limited" in cap.err
    assert [r["repo"] for r in json.loads(cap.out)] == ["a/a"]


def test_releases_all_failing_exits_3(monkeypatch, tmp_path, capsys):
    import pytest
    _setup(monkeypatch, tmp_path, {"a/a": RuntimeError("x")}, ["a/a"], seen={})
    with pytest.raises(SystemExit) as e:
        fetch_sources.releases()
    assert e.value.code == 3
    assert json.loads(capsys.readouterr().out) == []


def test_commit_state_moves_file(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_sources, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(fetch_sources, "TMP_DIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "releases.state.json").write_text('{"a": []}')
    (tmp_path / "tmp" / "feeds.state.json").write_text('{"u": "d"}')
    fetch_sources.commit_state("releases")
    fetch_sources.commit_state("feeds")
    assert (tmp_path / "state" / "releases.json").read_text() == '{"a": []}'
    assert (tmp_path / "state" / "feeds_seen.json").read_text() == '{"u": "d"}'
    assert not (tmp_path / "tmp" / "releases.state.json").exists()


def test_commit_state_noop_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_sources, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(fetch_sources, "TMP_DIR", str(tmp_path / "tmp"))
    fetch_sources.commit_state("releases")
    assert not (tmp_path / "state" / "releases.json").exists()


def test_releases_repo_without_releases_is_quiet(monkeypatch, tmp_path, capsys):
    now = datetime.now(timezone.utc)
    pages = {"a/a": _atom("a/a", [("v1", now)]), "e/e": _atom("e/e", [])}
    _setup(monkeypatch, tmp_path, pages, ["a/a", "e/e"], seen={})
    fetch_sources.releases()
    cap = capsys.readouterr()
    assert cap.err == ""
    assert _state(tmp_path)["e/e"] == []

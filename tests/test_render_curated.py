import render_email as re_


def _item(i, src="hackernews", gist=None, url=None, hn=None, title=None):
    return {"id": f"id{i}", "sources": [src], "title": title or f"Title {i}",
            "url": url if url is not None else f"https://example.com/item/{i}",
            "meta": "10 pts", "hn_url": hn, "gist": gist or f"Gist number {i}", "tags": []}


def _cur(n_themes, per, gist_len=None, grouped=True, rest=0):
    items, themes, k = {}, [], 0
    for t in range(n_themes):
        ids = []
        for _ in range(per):
            g = ("g%d " % k) * (gist_len // 4) if gist_len else None
            it = _item(k, gist=g[:gist_len] if g else None, hn=f"https://news.ycombinator.com/item?id={k}")
            items[it["id"]] = it
            ids.append(it["id"])
            k += 1
        themes.append({"title": f"Theme {t}", "summary": f"Summary {t}", "item_ids": ids})
    rest_ids = []
    for _ in range(rest):
        it = _item(k, src="trending")
        items[it["id"]] = it
        rest_ids.append(it["id"])
        k += 1
    return {"date": "2026-10-29", "items": items, "themes": themes, "rest": rest_ids,
            "stats": {"items_in": len(items), "items_placed": len(items),
                      "sources": ["trending", "hackernews"], "grouped": grouped, "probe_ok": True}}


def test_full_has_every_item_link():
    cur = _cur(3, 4, rest=5)
    body, full = re_.render_curated(cur, "2026-10-29")
    for it in cur["items"].values():
        assert full.count(f'href="{it["url"]}"') == 1
    assert "ALSO TODAY (5)" in full
    assert "The complete brief is attached as curated-2026-10-29.html" in full
    assert "12 items from 2 sources, 3 themes" not in full  # items_in counts rest too
    assert "17 items from 2 sources, 3 themes" in full
    assert body == full  # fits entirely


def test_empty_url_renders_plain_gist():
    cur = _cur(1, 1)
    cur["items"]["id0"]["url"] = ""
    _, full = re_.render_curated(cur, "2026-10-29")
    assert "Gist number 0" in full and 'href=""' not in full


def _check_cut(cur, limit):
    body, _ = re_.render_curated(cur, "2026-10-29", body_limit=limit)
    line = f'{len(cur["themes"][0]["item_ids"])} items, see attachment'
    # floor: the smallest possible body, every theme reduced to header + summary + line
    floor = len(re_.render_curated(cur, "2026-10-29", body_limit=0)[0].encode())
    assert len(body.encode()) <= max(limit, floor)
    complete = 0
    cut_seen = False
    for th in cur["themes"]:
        urls = [cur["items"][i]["url"] for i in th["item_ids"]]
        present = [f'href="{u}"' in body for u in urls]
        assert th["title"].upper() in body and th["summary"] in body
        if all(present):
            assert not cut_seen, "a complete theme follows a cut one"
            complete += 1
        else:
            assert not any(present), "theme cut mid-row"
            cut_seen = True
    assert body.count(line) == len(cur["themes"]) - complete
    return complete


def test_body_cut_at_theme_boundary():
    complete = _check_cut(_cur(8, 20, gist_len=120), 30_000)
    assert 0 < complete < 8


def test_body_cut_when_even_stubs_exceed_limit():
    # 30 stubs alone are bigger than 20k: nothing fits, every theme is a stub
    assert _check_cut(_cur(30, 20, gist_len=120), 20_000) == 0


def test_attachment_has_every_item():
    cur = _cur(30, 20, gist_len=120)
    _, full = re_.render_curated(cur, "2026-10-29", body_limit=20_000)
    for it in cur["items"].values():
        assert full.count(f'href="{it["url"]}"') == 1
    assert len(cur["items"]) == 600


def test_also_today_in_body_becomes_stub_when_over_limit():
    cur = _cur(2, 3, rest=40)
    body, full = re_.render_curated(cur, "2026-10-29", body_limit=len(re_.render_curated(_cur(2, 3), "d")[1].encode()) + 200)
    assert "ALSO TODAY (40)" in body and "40 items, see attachment" in body
    assert cur["items"][cur["rest"][0]]["url"] not in body
    assert cur["items"][cur["rest"][0]]["url"] in full


def test_ungrouped_renders_per_source():
    cur = _cur(0, 0)
    for i, src in enumerate(["trending", "hackernews", "trending"]):
        it = _item(i, src=src)
        cur["items"][it["id"]] = it
    cur["stats"].update(grouped=False, items_in=3)
    _, full = re_.render_curated(cur, "2026-10-29")
    assert "TRENDING (2)" in full and "HACKERNEWS (1)" in full
    assert full.index("TRENDING (2)") < full.index("HACKERNEWS (1)")
    assert "ungrouped" in full and "ALSO TODAY" not in full
    for it in cur["items"].values():
        assert full.count(f'href="{it["url"]}"') == 1


def test_hn_comments_link_and_title_line():
    cur = _cur(1, 1)
    it = cur["items"]["id0"]
    it["title"] = "owner/repo"
    _, full = re_.render_curated(cur, "2026-10-29")
    assert f'href="{it["hn_url"]}"' in full and ">comments<" in full
    assert "owner/repo" in full


def test_no_em_dash_curated():
    cur = _cur(3, 3, rest=3)
    for v in re_.render_curated(cur, "2026-10-29", body_limit=2000):
        assert chr(0x2014) not in v and "&mdash;" not in v


def test_item_in_two_themes_and_rest_renders_once():
    cur = _cur(2, 2, rest=0)
    dup = cur["themes"][0]["item_ids"][0]
    cur["themes"][1]["item_ids"].append(dup)
    cur["rest"] = [dup]
    _, full = re_.render_curated(cur, "2026-10-29")
    for it in cur["items"].values():
        assert full.count(f'href="{it["url"]}"') == 1
    assert "ALSO TODAY" not in full


def test_unplaced_item_falls_into_also_today():
    cur = _cur(2, 2)
    orphan = _item(99, src="trending")
    cur["items"][orphan["id"]] = orphan
    _, full = re_.render_curated(cur, "2026-10-29")
    assert full.count(f'href="{orphan["url"]}"') == 1
    assert full.index("ALSO TODAY (1)") < full.index(orphan["url"])
    assert "TRENDING (1)" in full

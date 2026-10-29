import json, os, sys

import curate

EXPECTED_ITEMS = 36
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "day")


def _day():
    out = {}
    for k in curate.KEYS:
        with open(os.path.join(FIX, k + ".json")) as f:
            out[k] = json.load(f)
    return out


def test_canonical_url_variants():
    variants = [
        "http://example.com/a/b",
        "https://www.example.com/a/b/",
        "HTTPS://Example.COM/a/b?utm_source=x&ref=y&fbclid=z",
        "https://example.com/a/b#frag",
    ]
    assert len({curate.canonical_url(u) for u in variants}) == 1
    assert curate.canonical_url(variants[0]) == "https://example.com/a/b"


def test_canonical_url_keeps_path():
    assert curate.canonical_url("https://github.com/a/b") != curate.canonical_url(
        "https://github.com/a/b/releases/tag/v1")
    assert curate.canonical_url("https://github.com/Foo/Bar").endswith("/Foo/Bar")


def test_canonical_url_keeps_other_params_sorted():
    assert curate.canonical_url("https://e.com/p?b=2&a=1&utm_medium=m") == "https://e.com/p?a=1&b=2"


def test_item_id_stable():
    a = curate.item_id("http://www.example.com/x/")
    assert a == curate.item_id("https://example.com/x")
    assert len(a) == 12


def test_normalise_merges_hn_and_feed_same_article():
    items = curate.normalise(_day())
    m = [i for i in items if i["url"].endswith("feed-article")]
    assert len(m) == 1
    assert m[0]["sources"] == ["hackernews", "feeds"]
    assert m[0]["hn_url"].startswith("https://news.ycombinator.com/")


def test_normalise_merges_hnbest_variant_url():
    items = curate.normalise(_day())
    m = [i for i in items if "shared-story" in i["url"]]
    assert len(m) == 1
    assert m[0]["sources"] == ["hackernews", "hnbest"]
    assert " · " in m[0]["meta"]


def test_normalise_trending_repo_once_across_periods():
    items = curate.normalise(_day())
    edge = [i for i in items if i["url"].endswith("Edge0-AI/Edge0")]
    assert len(edge) == 1
    assert "daily" in edge[0]["meta"] and "weekly" in edge[0]["meta"]
    assert edge[0]["sources"] == ["trending"]
    tool = [i for i in items if i["url"].endswith("Acme/Tool")]
    assert len(tool) == 1 and tool[0]["sources"] == ["trending", "newrepos"]


def test_normalise_reddit_self_post_and_null_counts():
    items = curate.normalise(_day())
    selfp = [i for i in items if i["title"] == "Self post"][0]
    assert selfp["url"] == "https://www.reddit.com/r/rust/comments/def/self/"
    assert selfp["meta"] == "r/rust"
    assert [i for i in items if i["title"] == "Third"][0]["meta"] == "r/python · 7 pts"


def test_normalise_meta_has_no_em_dash_and_shape():
    for i in curate.normalise(_day()):
        assert set(i) == {"id", "sources", "title", "url", "meta", "hn_url"}
        assert chr(0x2014) not in i["meta"]
        assert i["title"]


def _fixture_rows(day):
    rows = []
    for k, d in day.items():
        for r in ([r for v in d.values() for r in v] if isinstance(d, dict) else d):
            rows.append(r)
    return rows


def test_normalise_counts_match_fixture():
    day = _day()
    rows = _fixture_rows(day)
    items = curate.normalise(day)
    ids = [i["id"] for i in items]
    assert len(set(ids)) == len(ids)
    # every fixture row lands in exactly one item, none vanish
    hits = [sum(1 for i in items if curate.item_id(r["url"]) == i["id"]) for r in rows]
    assert hits == [1] * len(rows)
    assert sum(sum(1 for r in rows if curate.item_id(r["url"]) == i["id"]) for i in items) == len(rows)
    assert len(items) == EXPECTED_ITEMS


def test_normalise_keeps_urlless_rows(capsys):
    day = {"hackernews": [{"title": "Ask HN: x", "url": "", "points": 5, "comments": 2,
                           "hn_url": "https://news.ycombinator.com/item?id=9"}],
           "feeds": [{"title": "No link", "url": None, "feed": "F", "published": ""}],
           "hnbest": [{"title": "Ask HN: x", "url": None, "points": 1, "comments": 1, "hn_url": None}]}
    items = curate.normalise(day)
    assert len(items) == 3
    assert [i["url"] for i in items] == ["https://news.ycombinator.com/item?id=9", "", ""]
    assert items[1]["id"] == curate.item_id_for_text("hnbest", "Ask HN: x")
    assert items[2]["id"] == curate.item_id_for_text("feeds", "No link")
    assert len({i["id"] for i in items}) == 3
    assert capsys.readouterr().err.count("row without url kept") == 3


def test_urlless_trending_row_ok():
    day = {"trending": {"daily": [{"full_name": "a/b", "url": None, "stars": 1, "stars_gained": 1}]}}
    assert len(curate.normalise(day)) == 1


def test_canonical_url_malformed_port():
    assert curate.canonical_url("http://h.example:abc/p/") == "https://h.example/p"


def test_normalise_order_follows_key_order():
    items = curate.normalise(_day())
    assert items[0]["sources"][0] == "trending"
    assert items[-1]["sources"][0] == "reddit"


def test_load_day_missing_keys_skipped(tmp_path, capsys):
    (tmp_path / "2026-10-29-hackernews.json").write_text("[]")
    (tmp_path / "2026-10-29-releases.json").write_text("[1]")
    (tmp_path / "2026-10-29-feeds.json").write_text("{not json")
    day = curate.load_day(str(tmp_path), "2026-10-29")
    assert day == {"hackernews": []}
    assert "curate: feeds unreadable:" in capsys.readouterr().err

import os
from datetime import timezone

import feedparse

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def test_parse_rss2_items():
    items = feedparse.parse_feed(fx("rss2.xml"))
    assert len(items) == 3
    assert items[0]["title"] == "Hello & <welcome>"
    assert items[0]["published"].endswith("+00:00")
    assert items[0]["published"].startswith("2026-10-28T05:30:00")
    assert items[0]["summary"] == "First & foremost post"
    assert items[1]["id"] == "post-b"
    assert items[2]["published"] == ""


def test_parse_atom_entries():
    items = feedparse.parse_feed(fx("atom.xml"))
    assert len(items) == 2
    assert items[0]["url"] == "https://github.com/o/r/releases/tag/v1.0"
    assert items[0]["published"].startswith("2026-10-20T12:00:00")
    assert items[0]["summary"] == "Release notes"
    assert items[1]["url"] == "https://github.com/o/r/releases/tag/v0.9"
    assert items[1]["published"].startswith("2026-10-10T07:00:00")


def test_parse_feed_html_garbage_returns_empty():
    assert feedparse.parse_feed(fx("garbage.html")) == []


def test_read_opml_nested():
    feeds = feedparse.read_opml(os.path.join(FX, "sample.opml"))
    assert feeds == [
        {"title": "Blog One", "xmlUrl": "https://one.example/feed.xml"},
        {"title": "Blog Two", "xmlUrl": "https://two.example/atom"},
    ]


def test_parse_date_variants():
    a = feedparse.parse_date("Tue, 28 Oct 2026 10:30:00 +0500")
    b = feedparse.parse_date("2026-10-20T12:00:00Z")
    c = feedparse.parse_date("2026-10-10T09:00:00+02:00")
    assert a.utcoffset() == timezone.utc.utcoffset(None) and a.hour == 5
    assert b.tzinfo is not None and b.hour == 12
    assert c.hour == 7
    assert feedparse.parse_date("junk") is None
    assert feedparse.parse_date("") is None

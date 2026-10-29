import json
from datetime import datetime, timedelta, timezone

import pytest

import fetch_sources


def _rss(items):
    body = ""
    for title, url, when in items:
        pub = f"<pubDate>{when.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>" if when else ""
        body += f"<item><title>{title}</title><link>{url}</link>{pub}<description>d {title}</description></item>"
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


def _setup(monkeypatch, tmp_path, pages, seen=None):
    opml = tmp_path / "feeds.opml"
    outlines = "".join(f'<outline type="rss" title="F{i}" xmlUrl="http://f{i}/"/>' for i in range(len(pages)))
    opml.write_text(f'<?xml version="1.0"?><opml version="2.0"><body>{outlines}</body></opml>')
    state = tmp_path / "state"
    state.mkdir()
    if seen is not None:
        (state / "feeds_seen.json").write_text(json.dumps(seen))
    monkeypatch.setattr(fetch_sources, "FEEDS_OPML", str(opml))
    monkeypatch.setattr(fetch_sources, "STATE_DIR", str(state))
    monkeypatch.setattr(fetch_sources, "TMP_DIR", str(tmp_path / "tmp"))

    def fake_get(url, headers=None):
        page = pages[int(url.split("//f")[1].rstrip("/"))]
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(fetch_sources, "get", fake_get)


def test_feeds_window_26h(monkeypatch, tmp_path, capsys):
    now = datetime.now(timezone.utc)
    page = _rss([("fresh", "http://a/1", now - timedelta(hours=25)),
                 ("stale", "http://a/2", now - timedelta(hours=27))])
    _setup(monkeypatch, tmp_path, [page])
    fetch_sources.feeds()
    out = json.loads(capsys.readouterr().out)
    assert [r["url"] for r in out] == ["http://a/1"]
    assert out[0]["feed"] == "F0" and out[0]["title"] == "fresh"
    assert set(out[0]) == {"title", "url", "feed", "published", "summary"}


def test_feeds_undated_sent_once(monkeypatch, tmp_path, capsys):
    page = _rss([("nodate", "http://a/1", None)])
    _setup(monkeypatch, tmp_path, [page], seen={})
    fetch_sources.feeds()
    assert [r["url"] for r in json.loads(capsys.readouterr().out)] == ["http://a/1"]
    st = json.loads((tmp_path / "tmp" / "feeds.state.json").read_text())
    assert "http://a/1" in st

    (tmp_path / "state" / "feeds_seen.json").write_text(json.dumps(st))
    fetch_sources.feeds()
    assert json.loads(capsys.readouterr().out) == []


def test_feeds_state_pruned_30_days(monkeypatch, tmp_path, capsys):
    old = (datetime.now(timezone.utc) - timedelta(days=31)).date().isoformat()
    _setup(monkeypatch, tmp_path, [_rss([("n", "http://a/1", None)])], seen={"http://old": old})
    fetch_sources.feeds()
    st = json.loads((tmp_path / "tmp" / "feeds.state.json").read_text())
    assert "http://old" not in st


def test_feeds_one_bad_feed_does_not_kill_run(monkeypatch, tmp_path, capsys):
    now = datetime.now(timezone.utc)
    good = _rss([("ok", "http://a/1", now - timedelta(hours=1))])
    _setup(monkeypatch, tmp_path, [good, "<html>garbage</html>"])
    fetch_sources.feeds()
    cap = capsys.readouterr()
    assert [r["url"] for r in json.loads(cap.out)] == ["http://a/1"]
    assert len(cap.err.strip().splitlines()) == 1
    assert cap.err.startswith("feeds: F1 failed:")


def test_feeds_all_failed_exit_3(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, [OSError("down"), "garbage"])
    with pytest.raises(SystemExit) as e:
        fetch_sources.feeds()
    assert e.value.code == 3
    cap = capsys.readouterr()
    assert json.loads(cap.out) == []
    assert len(cap.err.strip().splitlines()) == 2


def test_feeds_dedupes_across_feeds(monkeypatch, tmp_path, capsys):
    now = datetime.now(timezone.utc)
    page = _rss([("same", "http://a/1", now - timedelta(hours=1))])
    _setup(monkeypatch, tmp_path, [page, page])
    fetch_sources.feeds()
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1 and out[0]["feed"] == "F0"


def test_get_decompresses_unrequested_gzip(monkeypatch):
    import gzip, io
    body = gzip.compress(b"<rss>hi</rss>")

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(fetch_sources.urllib.request, "urlopen", lambda req, timeout=0: R(body))
    assert fetch_sources.get("http://x/") == "<rss>hi</rss>"

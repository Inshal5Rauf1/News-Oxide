import json
import urllib.error
from html import escape

import pytest

import fetch_sources

PERMA = "https://www.reddit.com/r/{sub}/comments/{id}/{slug}/"


def _entry(title, permalink, external=None):
    """One Atom entry shaped like Reddit's hot.rss: footer [link] points at the post's
    external URL, or at the permalink itself for a self post."""
    target = external or permalink
    body = (f'<div class="md"><p>body of {title}</p></div>'
            f'<span><a href="{target}">[link]</a></span> '
            f'<span><a href="{permalink}">[comments]</a></span>')
    return (f"<entry><title>{escape(title)}</title><link href=\"{permalink}\"/>"
            f"<id>{permalink}</id><updated>2026-10-29T10:00:00+00:00</updated>"
            f"<content type=\"html\">{escape(body)}</content></entry>")


def _feed(entries):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title>'
            + "".join(entries) + "</feed>")


def _429():
    return urllib.error.HTTPError("https://www.reddit.com/", 429, "Too Many Requests", None, None)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Record every time.sleep call instead of sleeping, so pacing costs nothing in tests."""
    calls = []
    monkeypatch.setattr(fetch_sources.time, "sleep", calls.append)
    return calls


def _setup(monkeypatch, tmp_path, subs, pages):
    """pages: {sub: feed XML str, Exception, or a list of those consumed one per request}.
    subs go into a fake subreddits.txt."""
    txt = tmp_path / "subreddits.txt"
    txt.write_text("\n".join(subs) + "\n")
    monkeypatch.setattr(fetch_sources, "SUBREDDITS_TXT", str(txt))
    seen_headers = []

    def fake_get(url, headers=None):
        seen_headers.append((url, headers))
        sub = url.split("/r/")[1].split("/")[0]
        page = pages[sub]
        if isinstance(page, list):
            page = page.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(fetch_sources, "get", fake_get)
    return seen_headers


def test_reddit_external_link_vs_self_post(monkeypatch, tmp_path, capsys):
    link_perma = PERMA.format(sub="x", id="1", slug="link")
    self_perma = PERMA.format(sub="x", id="2", slug="selfie")
    page = _feed([
        _entry("link post", link_perma, external="https://ext.example/post?a=1&b=2"),
        _entry("selfie", self_perma),
    ])
    _setup(monkeypatch, tmp_path, ["x"], {"x": page})
    fetch_sources.reddit()
    out = json.loads(capsys.readouterr().out)
    assert [r["title"] for r in out] == ["link post", "selfie"]
    assert set(out[0]) == {"title", "url", "subreddit", "score", "comments", "permalink"}
    assert out[0]["url"] == "https://ext.example/post?a=1&b=2"
    assert out[0]["permalink"] == link_perma
    assert out[0]["subreddit"] == "x"
    assert out[0]["score"] is None and out[0]["comments"] is None
    assert out[1]["url"] == self_perma and out[1]["permalink"] == self_perma


def test_reddit_one_sub_fails_others_survive(monkeypatch, tmp_path, capsys):
    good = _feed([_entry("ok", PERMA.format(sub="b", id="1", slug="ok"))])
    _setup(monkeypatch, tmp_path, ["# comment line", "", "a", "b"],
           {"a": RuntimeError("HTTP Error 403:\n  Forbidden"), "b": good})
    fetch_sources.reddit()
    cap = capsys.readouterr()
    assert [r["title"] for r in json.loads(cap.out)] == ["ok"]
    assert cap.err.splitlines() == ["reddit: r/a failed: HTTP Error 403: Forbidden"]


def test_reddit_all_fail_exit_3(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, ["a", "b"],
           {"a": RuntimeError("boom"), "b": RuntimeError("boom")})
    with pytest.raises(SystemExit) as exc:
        fetch_sources.reddit()
    assert exc.value.code == 3
    cap = capsys.readouterr()
    assert json.loads(cap.out) == []
    assert len(cap.err.splitlines()) == 2


def test_reddit_user_agent_header(monkeypatch, tmp_path, capsys):
    seen = _setup(monkeypatch, tmp_path, ["x"], {"x": _feed([])})
    with pytest.raises(SystemExit):
        fetch_sources.reddit()   # empty feed counts as a failed sub, so exit 3
    assert seen == [("https://www.reddit.com/r/x/hot.rss",
                     {"User-Agent": "news-oxide/1.0 (personal daily brief; by u/inshal)"})]


def test_reddit_dedups_crossposts_first_sub_wins(monkeypatch, tmp_path, capsys):
    shared = "https://ext.example/same-story"
    a = _feed([_entry("from a", PERMA.format(sub="a", id="1", slug="x"), external=shared)])
    b = _feed([_entry("from b", PERMA.format(sub="b", id="2", slug="x"), external=shared)])
    _setup(monkeypatch, tmp_path, ["a", "b"], {"a": a, "b": b})
    fetch_sources.reddit()
    out = json.loads(capsys.readouterr().out)
    assert [(r["title"], r["subreddit"]) for r in out] == [("from a", "a")]


def test_reddit_429_retries_once_after_pause(monkeypatch, tmp_path, capsys, sleeps):
    good = _feed([_entry("ok", PERMA.format(sub="x", id="1", slug="ok"))])
    seen = _setup(monkeypatch, tmp_path, ["x"], {"x": [_429(), good]})
    fetch_sources.reddit()
    out = json.loads(capsys.readouterr().out)
    assert [r["title"] for r in out] == ["ok"]
    assert len(seen) == 2
    assert sleeps == [fetch_sources.REDDIT_PAUSE * 4]


def test_reddit_second_429_is_that_sub_failing(monkeypatch, tmp_path, capsys, sleeps):
    good = _feed([_entry("ok", PERMA.format(sub="b", id="1", slug="ok"))])
    seen = _setup(monkeypatch, tmp_path, ["a", "b"], {"a": [_429(), _429()], "b": good})
    fetch_sources.reddit()
    cap = capsys.readouterr()
    assert [r["title"] for r in json.loads(cap.out)] == ["ok"]
    assert cap.err.splitlines() == ["reddit: r/a failed: HTTP Error 429: Too Many Requests"]
    assert len(seen) == 3
    assert sleeps == [fetch_sources.REDDIT_PAUSE * 4, fetch_sources.REDDIT_PAUSE]


def test_reddit_paces_between_subs_not_before_first(monkeypatch, tmp_path, capsys, sleeps):
    good = _feed([_entry("t", PERMA.format(sub="x", id="1", slug="t"))])
    _setup(monkeypatch, tmp_path, ["a", "b", "c"], {"a": good, "b": good, "c": good})
    fetch_sources.reddit()
    assert sleeps == [fetch_sources.REDDIT_PAUSE, fetch_sources.REDDIT_PAUSE]

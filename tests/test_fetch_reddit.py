import json

import pytest

import fetch_sources


def _listing(posts):
    children = [{"kind": "t3", "data": p} for p in posts]
    return json.dumps({"kind": "Listing", "data": {"children": children}})


def _post(title, permalink, *, url=None, is_self=False, stickied=False, score=10, comments=3):
    return {"title": title, "permalink": permalink, "url": url or f"https://example.com/{title}",
            "is_self": is_self, "stickied": stickied, "score": score, "num_comments": comments}


def _setup(monkeypatch, tmp_path, subs, pages):
    """pages: {sub: listing JSON str or Exception}; subs written to a fake subreddits.txt."""
    txt = tmp_path / "subreddits.txt"
    txt.write_text("\n".join(subs) + "\n")
    monkeypatch.setattr(fetch_sources, "SUBREDDITS_TXT", str(txt))
    seen_headers = []

    def fake_get(url, headers=None):
        seen_headers.append(headers)
        sub = url.split("/r/")[1].split("/")[0]
        page = pages[sub]
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(fetch_sources, "get", fake_get)
    return seen_headers


def test_reddit_shape_skips_stickied(monkeypatch, tmp_path, capsys):
    page = _listing([
        _post("pinned", "/r/x/comments/1/pinned/", stickied=True),
        _post("link", "/r/x/comments/2/link/", url="https://ext.example/post", score=42, comments=7),
        _post("selfie", "/r/x/comments/3/selfie/", is_self=True, url="https://www.reddit.com/r/x/comments/3/selfie/"),
    ])
    _setup(monkeypatch, tmp_path, ["x"], {"x": page})
    fetch_sources.reddit()
    out = json.loads(capsys.readouterr().out)
    assert [r["title"] for r in out] == ["link", "selfie"]
    assert set(out[0]) == {"title", "url", "subreddit", "score", "comments", "permalink"}
    assert out[0]["url"] == "https://ext.example/post"
    assert out[0]["permalink"] == "https://www.reddit.com/r/x/comments/2/link/"
    assert out[0]["subreddit"] == "x" and out[0]["score"] == 42 and out[0]["comments"] == 7
    assert out[1]["url"] == "https://www.reddit.com/r/x/comments/3/selfie/"


def test_reddit_one_sub_fails_others_survive(monkeypatch, tmp_path, capsys):
    good = _listing([_post("ok", "/r/b/comments/1/ok/")])
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
    seen = _setup(monkeypatch, tmp_path, ["x"], {"x": _listing([])})
    fetch_sources.reddit()
    assert seen == [{"User-Agent": "news-oxide/1.0 (personal daily brief; by u/inshal)"}]


def test_reddit_dedups_crossposts_first_sub_wins(monkeypatch, tmp_path, capsys):
    shared = "https://ext.example/same-story"
    a = _listing([_post("from a", "/r/a/comments/1/x/", url=shared)])
    b = _listing([_post("from b", "/r/b/comments/2/x/", url=shared)])
    _setup(monkeypatch, tmp_path, ["a", "b"], {"a": a, "b": b})
    fetch_sources.reddit()
    out = json.loads(capsys.readouterr().out)
    assert [(r["title"], r["subreddit"]) for r in out] == [("from a", "a")]

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import fetch_sources


def _canned_repos(n=2):
    return {"items": [
        {"full_name": f"owner/repo{i}", "html_url": f"https://github.com/owner/repo{i}",
         "description": f"desc {i}", "language": "Python", "stargazers_count": 100 + i,
         "created_at": "2026-10-25T00:00:00Z", "extra": "ignored"}
        for i in range(n)
    ]}


def test_newrepos_shape(monkeypatch, capsys):
    seen = {}

    def fake_get(url, headers=None):
        seen["url"] = url
        seen["headers"] = headers
        return json.dumps(_canned_repos())

    monkeypatch.setattr(fetch_sources, "get", fake_get)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    fetch_sources.newrepos()

    decoded = unquote(seen["url"])
    assert decoded.startswith("https://api.github.com/search/repositories?q=")
    assert "created:>=" in decoded
    assert "stars:>=100" in decoded
    assert "sort=stars" in decoded and "order=desc" in decoded and "per_page=100" in decoded
    assert " " not in seen["url"] and ">" not in seen["url"]
    assert seen["headers"] is None

    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2
    assert set(out[0]) == {"full_name", "url", "description", "language", "stars", "created_at"}
    assert out[0] == {"full_name": "owner/repo0", "url": "https://github.com/owner/repo0",
                      "description": "desc 0", "language": "Python", "stars": 100,
                      "created_at": "2026-10-25T00:00:00Z"}


def test_newrepos_query_date_is_seven_days_ago(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(fetch_sources, "get", lambda url, headers=None: seen.update(url=url) or json.dumps({"items": []}))
    fetch_sources.newrepos()
    capsys.readouterr()
    expected = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
    assert f"created:>={expected}" in unquote(seen["url"])


def test_newrepos_sends_bearer_when_token_set(monkeypatch, capsys):
    seen = {}

    def fake_get(url, headers=None):
        seen["headers"] = headers
        return json.dumps({"items": []})

    monkeypatch.setattr(fetch_sources, "get", fake_get)
    monkeypatch.setenv("GITHUB_TOKEN", "tok123")
    fetch_sources.newrepos()
    capsys.readouterr()
    assert seen["headers"] == {"Authorization": "Bearer tok123"}


def test_newrepos_failure_prints_empty_list(monkeypatch, capsys):
    def boom(url, headers=None):
        raise OSError("network down")

    monkeypatch.setattr(fetch_sources, "get", boom)
    fetch_sources.newrepos()
    assert json.loads(capsys.readouterr().out) == []


def test_newrepos_caps_at_100(monkeypatch, capsys):
    monkeypatch.setattr(fetch_sources, "get", lambda url, headers=None: json.dumps(_canned_repos(150)))
    fetch_sources.newrepos()
    assert len(json.loads(capsys.readouterr().out)) == 100


def _hn_hit(oid, title, points, comments, url=None, author="pg"):
    return {"objectID": str(oid), "title": title, "points": points, "num_comments": comments,
            "url": url, "author": author}


def test_hnbest_output_shape_and_sort(monkeypatch, capsys):
    hits = {"hits": [
        _hn_hit(1, "low", 10, 2, url="https://example.com/a"),
        _hn_hit(2, "high", 500, 90, url=None),
        _hn_hit(3, "mid", 120, 30, url="https://example.com/c"),
    ]}
    monkeypatch.setattr(fetch_sources, "get", lambda url, headers=None: json.dumps(hits))
    fetch_sources.hnbest()
    out = json.loads(capsys.readouterr().out)
    assert [o["title"] for o in out] == ["high", "mid", "low"]
    assert set(out[0]) == {"title", "url", "points", "comments", "hn_url", "author"}
    assert out[0]["url"] == "https://news.ycombinator.com/item?id=2"
    assert out[0]["hn_url"] == "https://news.ycombinator.com/item?id=2"
    assert out[1]["url"] == "https://example.com/c"


def test_hnbest_epoch_is_24h_ago(monkeypatch, capsys):
    import time
    seen = {}
    monkeypatch.setattr(fetch_sources, "get", lambda url, headers=None: seen.update(url=url) or json.dumps({"hits": []}))
    fetch_sources.hnbest()
    capsys.readouterr()
    epoch = int(seen["url"].split("created_at_i>")[1].split("&")[0])
    now = int(time.time())
    assert now - 86400 - 60 <= epoch <= now - 86400 + 60


def test_hnbest_failure_prints_empty_list(monkeypatch, capsys):
    def boom(url, headers=None):
        raise OSError("network down")

    monkeypatch.setattr(fetch_sources, "get", boom)
    fetch_sources.hnbest()
    assert json.loads(capsys.readouterr().out) == []

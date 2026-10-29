#!/usr/bin/env python3
"""
fetch_sources.py: deterministic snapshot sources (no API keys, no LLM).
  trending     GitHub trending repos (Trendshift mirror: daily/weekly/monthly/yearly)
  huggingface  trending models / datasets / spaces + daily papers (HF API)
  hackernews   top front-page stories (HN Algolia API)
  newrepos     GitHub repos created in the last 7 days with >=100 stars
  hnbest       top HN stories from the last 24h, by points
  feeds        curated RSS/Atom feeds from feeds.opml (last 26h, plus unseen undated items)
Each prints a JSON snapshot to stdout; render_email.py renders it as-is.
"""
import sys, gzip, io, json, os, re, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

import feedparse

_HERE = os.path.dirname(os.path.abspath(__file__))
FEEDS_OPML = os.path.join(_HERE, "feeds.opml")
STATE_DIR = os.path.join(_HERE, "state")
TMP_DIR = os.path.join(_HERE, "tmp")

UA = {"User-Agent": "Mozilla/5.0 (daily-brief research script)"}
MAX_BYTES = 8 * 1024 * 1024            # cap response size as a blowup defense


def get(url, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=40) as r:
        raw = r.read(MAX_BYTES)
    if raw[:2] == b"\x1f\x8b":        # some servers gzip even without Accept-Encoding
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(MAX_BYTES)
    return raw.decode("utf-8", "replace")


def _unescape_rsc(html):
    """Concatenate Next.js self.__next_f.push([1,"..."]) string payloads, unescaped."""
    out = []
    for p in re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html, re.S):
        try:
            out.append(json.loads(p))
        except Exception:
            pass
    return "".join(out)


def _extract_initialdata(text):
    """Find "initialData":[ ... ] and return the parsed JSON array (bracket-matched)."""
    idx = text.find('"initialData":')
    if idx == -1:
        return []
    start = text.find('[', idx)
    if start == -1:
        return []
    depth = 0; instr = False; esc = False
    for i in range(start, len(text)):
        c = text[i]
        if esc:
            esc = False
        elif c == '\\':
            esc = True
        elif c == '"':
            instr = not instr
        elif not instr:
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        return []
    return []


def trending():
    periods = {
        "daily": "https://trendshift.io/",
        "weekly": "https://trendshift.io/weekly",
        "monthly": "https://trendshift.io/monthly",
        "yearly": "https://trendshift.io/yearly",
    }
    out = {}
    for name, url in periods.items():
        repos = []
        try:
            data = _extract_initialdata(_unescape_rsc(get(url)))
            for d in data:
                fn = d.get("full_name")
                if not fn:
                    continue
                repos.append({
                    "rank": d.get("rank"),
                    "full_name": fn,
                    "url": f"https://github.com/{fn}",
                    "description": d.get("repository_description") or "",
                    "language": d.get("repository_language") or d.get("language") or "",
                    "stars": d.get("repository_stars"),
                    "stars_gained": d.get("repository_stars_gained"),
                    "tags": [t.get("display_name") for t in (d.get("tags") or []) if t.get("display_name")][:4],
                })
        except Exception:
            repos = []   # empty on failure → that period just shows 0, no fake item
        out[name] = repos
    print(json.dumps(out))


def _hf(path):
    return json.loads(get("https://huggingface.co/api/" + path))


def huggingface():
    out = {"models": [], "datasets": [], "spaces": [], "papers": []}
    try:
        for m in _hf("models?sort=trendingScore&limit=20"):
            out["models"].append({"id": m.get("id"), "url": "https://huggingface.co/" + m.get("id", ""),
                                  "likes": m.get("likes"), "downloads": m.get("downloads"),
                                  "pipeline_tag": m.get("pipeline_tag") or ""})
    except Exception:
        out["models"] = []   # empty on failure → skipped, not an empty-section email
    try:
        for d in _hf("datasets?sort=trendingScore&limit=12"):
            out["datasets"].append({"id": d.get("id"), "url": "https://huggingface.co/datasets/" + d.get("id", ""),
                                    "likes": d.get("likes"), "downloads": d.get("downloads")})
    except Exception:
        pass
    try:
        for s in _hf("spaces?sort=trendingScore&limit=12"):
            out["spaces"].append({"id": s.get("id"), "url": "https://huggingface.co/spaces/" + s.get("id", ""),
                                  "likes": s.get("likes"), "sdk": s.get("sdk") or ""})
    except Exception:
        pass
    try:
        for p in _hf("daily_papers?limit=15"):
            paper = p.get("paper", {}) if isinstance(p.get("paper"), dict) else {}
            pid = paper.get("id") or p.get("id") or ""
            authors = paper.get("authors") or p.get("authors") or []
            names = ", ".join(a.get("name", "") for a in authors[:4] if isinstance(a, dict))
            out["papers"].append({"title": p.get("title") or paper.get("title", ""),
                                  "url": "https://huggingface.co/papers/" + pid,
                                  "upvotes": paper.get("upvotes") or p.get("upvotes"), "authors": names})
    except Exception:
        pass
    print(json.dumps(out))


def _hn_row(h):
    """One HN Algolia hit as the six-key row shared by hackernews() and hnbest()."""
    oid = h.get("objectID", "")
    return {"title": h.get("title"),
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
            "points": h.get("points"), "comments": h.get("num_comments"),
            "hn_url": f"https://news.ycombinator.com/item?id={oid}", "author": h.get("author")}


def hackernews():
    out = []
    try:
        d = json.loads(get("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30"))
        out = [_hn_row(h) for h in d.get("hits", [])]
    except Exception:
        out = []   # emit empty on failure so the topic is skipped, not sent as an empty email
    print(json.dumps(out))


def newrepos():
    out = []
    try:
        since = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
        q = urllib.parse.quote(f"created:>={since} stars:>=100", safe="")
        url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=100"
        token = os.environ.get("GITHUB_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else None
        for r in json.loads(get(url, headers=headers)).get("items", [])[:100]:
            out.append({"full_name": r.get("full_name"), "url": r.get("html_url"),
                        "description": r.get("description") or "", "language": r.get("language") or "",
                        "stars": r.get("stargazers_count"), "created_at": r.get("created_at")})
    except Exception:
        out = []   # empty on failure so the section is skipped, not sent as an empty list
    print(json.dumps(out))


def hnbest():
    out = []
    try:
        epoch = int(time.time()) - 86400
        url = f"https://hn.algolia.com/api/v1/search?tags=story&numericFilters=created_at_i>{epoch}&hitsPerPage=50"
        hits = json.loads(get(url)).get("hits", [])
        hits.sort(key=lambda h: h.get("points") or 0, reverse=True)
        out = [_hn_row(h) for h in hits]
    except Exception:
        out = []
    print(json.dumps(out))


def feeds():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=26)
    try:
        with open(os.path.join(STATE_DIR, "feeds_seen.json")) as f:
            seen = json.load(f)
    except (OSError, ValueError):
        seen = {}
    state = dict(seen)
    out, urls, failed = [], set(), 0
    sources = feedparse.read_opml(FEEDS_OPML)
    for src in sources:
        try:
            items = feedparse.parse_feed(get(src["xmlUrl"]))
            if not items:
                raise ValueError("no items parsed")
        except Exception as e:
            failed += 1
            print(f"feeds: {src['title']} failed: {e}", file=sys.stderr)
            continue
        for it in items:
            url = it["url"]
            if not url or url in urls:
                continue
            when = feedparse.parse_date(it["published"])
            if when is not None:
                keep = when >= cutoff
            else:
                keep = url not in seen
                state.setdefault(url, now.date().isoformat())
            if keep:
                urls.add(url)
                out.append({"title": it["title"], "url": url, "feed": src["title"],
                            "published": it["published"], "summary": it["summary"]})
    keep_after = (now - timedelta(days=30)).date().isoformat()
    state = {u: d for u, d in state.items() if d >= keep_after}
    os.makedirs(TMP_DIR, exist_ok=True)
    with open(os.path.join(TMP_DIR, "feeds.state.json"), "w") as f:
        json.dump(state, f)
    print(json.dumps(out))
    if sources and failed == len(sources):
        raise SystemExit(3)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    dispatch = {"trending": trending, "huggingface": huggingface, "hackernews": hackernews,
                "newrepos": newrepos, "hnbest": hnbest, "feeds": feeds}
    if cmd in dispatch:
        dispatch[cmd]()
    else:
        sys.exit("usage: fetch_sources.py trending|huggingface|hackernews|newrepos|hnbest|feeds")

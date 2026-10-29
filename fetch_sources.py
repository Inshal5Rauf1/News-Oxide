#!/usr/bin/env python3
"""
fetch_sources.py: deterministic snapshot sources (no API keys, no LLM).
  trending     GitHub trending repos (Trendshift mirror: daily/weekly/monthly/yearly)
  huggingface  trending models / datasets / spaces + daily papers (HF API)
  hackernews   top front-page stories (HN Algolia API)
  newrepos     GitHub repos created in the last 7 days with >=100 stars
  hnbest       top HN stories from the last 24h, by points
  feeds        curated RSS/Atom feeds from feeds.opml (last 26h, plus unseen undated items)
  reddit       hot posts from subreddits.txt (RSS; stickied posts not detectable)
  releases     new GitHub releases (last 7 days) for releases.txt + starred repos
  commit-state <feeds|releases>  promote tmp/<key>.state.json into state/
Each prints a JSON snapshot to stdout; render_email.py renders it as-is.
"""
import sys, gzip, io, json, os, re, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from datetime import datetime, timedelta, timezone

import feedparse

_HERE = os.path.dirname(os.path.abspath(__file__))
FEEDS_OPML = os.path.join(_HERE, "feeds.opml")
STATE_DIR = os.path.join(_HERE, "state")
TMP_DIR = os.path.join(_HERE, "tmp")
SUBREDDITS_TXT = os.path.join(_HERE, "subreddits.txt")
RELEASES_TXT = os.path.join(_HERE, "releases.txt")
REDDIT_UA = "news-oxide/1.0 (personal daily brief; by u/inshal)"
REDDIT_PAUSE = 10.0                    # seconds between subreddit requests

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


def _reddit_links(text):
    """Per Atom entry, in document order: the href of the footer [link] anchor in its
    HTML content (the external URL, or the permalink for a self post), else None."""
    out = []
    for e in ET.fromstring(text).iter():
        if not isinstance(e.tag, str) or e.tag.split("}")[-1] != "entry":
            continue
        content = next((c.text or "" for c in e if c.tag.split("}")[-1] == "content"), "")
        hrefs = re.findall(r'<a href="([^"]+)">\[link\]', content)
        out.append(unescape(hrefs[-1]) if hrefs else None)
    return out


def _reddit_get(sub):
    """One subreddit's RSS; a 429 is retried once after a longer pause."""
    url = f"https://www.reddit.com/r/{sub}/hot.rss"
    try:
        return get(url, headers={"User-Agent": REDDIT_UA})
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
    time.sleep(REDDIT_PAUSE * 4)
    return get(url, headers={"User-Agent": REDDIT_UA})


def reddit():
    with open(SUBREDDITS_TXT, encoding="utf-8") as f:
        subs = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    out, urls, failed = [], set(), 0
    for i, sub in enumerate(subs):
        if i:
            time.sleep(REDDIT_PAUSE)
        try:
            text = _reddit_get(sub)
            items = feedparse.parse_feed(text)
            if not items:
                raise ValueError("no entries parsed")
        except Exception as e:
            failed += 1
            print(f"reddit: r/{sub} failed: {' '.join(str(e).split())}", file=sys.stderr)
            continue
        for it, ext in zip(items, _reddit_links(text)):
            permalink = it["url"]
            link = ext if ext and ext != permalink else permalink
            if link in urls:          # crosspost: first subreddit wins
                continue
            urls.add(link)
            out.append({"title": it["title"], "url": link, "subreddit": sub,
                        "score": None, "comments": None, "permalink": permalink})
    print(json.dumps(out))
    if subs and failed == len(subs):
        raise SystemExit(3)

def _starred():
    """owner/repo of the user's starred repos (up to 3 pages of 100); [] and a stderr line on failure."""
    user = os.environ.get("GITHUB_USER", "Inshal5Rauf1")
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else None
    repos = []
    try:
        for page in (1, 2, 3):
            url = f"https://api.github.com/users/{user}/starred?per_page=100&page={page}"
            rows = json.loads(get(url, headers=headers))
            if not rows:
                break
            repos += [r["full_name"] for r in rows if r.get("full_name")]
    except Exception as e:
        print(f"releases: starred fetch failed: {' '.join(str(e).split())}", file=sys.stderr)
        return []
    return repos


def _release_entries(repo):
    text = get(f"https://github.com/{repo}/releases.atom")
    items = feedparse.parse_feed(text)
    if not items and "<feed" not in text:      # a valid feed with no releases yet is not a failure
        raise ValueError("no entries parsed")
    return items


def releases():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    try:
        with open(RELEASES_TXT, encoding="utf-8") as f:
            listed = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    except OSError:
        listed = []
    repos = sorted(set(listed) | set(_starred()))
    state_path = os.path.join(STATE_DIR, "releases.json")
    first_run = not os.path.exists(state_path)
    seen = {}
    if not first_run:
        try:
            with open(state_path) as f:
                seen = json.load(f)
            if not isinstance(seen, dict):
                raise ValueError("not a dict")
        except (OSError, ValueError):
            print("releases: state unreadable, reseeding", file=sys.stderr)
            seen, first_run = {}, True

    def fetch(repo):
        try:
            return _release_entries(repo)
        except Exception as e:
            print(f"releases: {repo} failed: {' '.join(str(e).split())}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch, repos))     # map keeps the sorted repo order

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    out, state, failed = [], {}, 0
    for repo, items in zip(repos, results):
        if items is None:
            failed += 1
            state[repo] = seen.get(repo, [])      # keep history so a blip does not re-announce
            continue
        items.sort(key=lambda it: feedparse.parse_date(it["published"]) or epoch, reverse=True)
        state[repo] = [it["id"] for it in items][:20]
        known = set(seen.get(repo, []))
        for it in items:
            when = feedparse.parse_date(it["published"])
            if first_run or it["id"] in known or when is None or when < cutoff:
                continue
            out.append({"repo": repo, "tag": urllib.parse.unquote(it["url"].rstrip("/").rsplit("/", 1)[-1]),
                        "name": it["title"], "url": it["url"], "published": it["published"],
                        "notes": it["summary"][:300]})
    out.sort(key=lambda r: feedparse.parse_date(r["published"]), reverse=True)
    if repos and failed == len(repos):
        print("[]")
        raise SystemExit(3)                       # no state file: never commit an empty state
    os.makedirs(TMP_DIR, exist_ok=True)
    with open(os.path.join(TMP_DIR, "releases.state.json"), "w") as f:
        json.dump(state, f)
    print(json.dumps(out))


_STATE_FILES = {"feeds": "feeds_seen.json", "releases": "releases.json"}


def commit_state(key):
    """Promote tmp/<key>.state.json to its state/ file; silent no-op if it is absent."""
    if key not in _STATE_FILES:
        sys.exit("usage: fetch_sources.py commit-state feeds|releases")
    src = os.path.join(TMP_DIR, f"{key}.state.json")
    if not os.path.exists(src):
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    os.replace(src, os.path.join(STATE_DIR, _STATE_FILES[key]))

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    dispatch = {"trending": trending, "huggingface": huggingface, "hackernews": hackernews,
                "newrepos": newrepos, "hnbest": hnbest, "feeds": feeds,
                "reddit": reddit, "releases": releases}
    if cmd == "commit-state":
        commit_state(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd in dispatch:
        dispatch[cmd]()
    else:
        sys.exit("usage: fetch_sources.py trending|huggingface|hackernews|newrepos|hnbest|feeds|reddit|releases|commit-state <feeds|releases>")

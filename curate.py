#!/usr/bin/env python3
"""
curate.py: the curate stage.
  load_day     read the archived sent/<date>-<key>.json snapshots
  normalise    flatten them into one item list, merging items with the same URL
  gists        pass 1 (haiku): one-line gist and tags per item, batched
  themes       pass 2 (sonnet): group gists into themes
  enforce      every item placed exactly once, whatever the model returned
  run / main   write sent/<date>-curated.json
Item: {id, sources, title, url, meta, hn_url, desc}. The model groups and summarises, never drops.
"""
import sys, hashlib, json, os, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor

import claude_cli

_HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS_DIR = os.path.join(_HERE, "prompts")

KEYS = ["trending", "hackernews", "hnbest", "huggingface", "newrepos", "feeds", "reddit"]
PERIODS = ["daily", "weekly", "monthly", "yearly"]
DROP_PARAMS = {"ref", "ref_src", "fbclid", "gclid"}


def canonical_url(url):
    raw = (url or "").strip()
    try:
        p = urllib.parse.urlsplit(raw)
    except ValueError:
        return raw                     # unparseable (e.g. bad IPv6 bracket): the raw string is the key
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        if p.port:
            host += ":%d" % p.port
    except ValueError:
        pass                           # malformed port: fall back to no port
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in DROP_PARAMS]
    path = p.path[:-1] if p.path.endswith("/") else p.path
    return urllib.parse.urlunsplit(("https", host, path, urllib.parse.urlencode(sorted(q)), ""))


def item_id(url):
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:12]


def item_id_for_text(key, title):
    return hashlib.sha1(("%s:%s" % (key, title)).encode()).hexdigest()[:12]


def load_day(sent_dir, date):
    day = {}
    for key in KEYS:
        path = os.path.join(sent_dir, "%s-%s.json" % (date, key))
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                day[key] = json.load(f)
        except (OSError, ValueError) as e:
            print("curate: %s unreadable: %s" % (key, e), file=sys.stderr)
    return day


def _n(v):
    return "{:,}".format(v) if isinstance(v, (int, float)) else "0"


def _join(*parts):
    return " · ".join(p for p in parts if p)


def _hn(rows):
    for r in rows:
        yield dict(title=r.get("title"), url=r.get("url"), hn_url=r.get("hn_url"), desc="",
                   meta="%s pts · %s comments" % (_n(r.get("points")), _n(r.get("comments"))))


def _trending(d):
    seen = {}
    for period in PERIODS:
        for r in d.get(period) or []:
            key = canonical_url(r["url"]) if r.get("url") else ("nourl", len(seen))
            if key in seen:
                seen[key]["periods"].append(period)
            else:
                seen[key] = dict(r=r, periods=[period])
    for e in seen.values():
        r = e["r"]
        yield dict(title=r.get("full_name"), url=r.get("url"), hn_url=None,
                   desc=r.get("description") or "",
                   meta=_join("★ %s ▲ %s" % (_n(r.get("stars")), _n(r.get("stars_gained"))),
                              r.get("language"), ", ".join(e["periods"])))


def _huggingface(d):
    for kind in ("models", "datasets", "spaces"):
        for r in d.get(kind) or []:
            yield dict(title=r.get("id"), url=r.get("url"), hn_url=None,
                       desc=r.get("pipeline_tag") or r.get("sdk") or "",
                       meta=_join("♥ %s" % _n(r.get("likes")),
                                  "↓ %s" % _n(r["downloads"]) if r.get("downloads") is not None else "",
                                  r.get("pipeline_tag") or r.get("sdk") or kind[:-1]))
    for r in d.get("papers") or []:
        yield dict(title=r.get("title"), url=r.get("url"), hn_url=None,
                   desc=r.get("authors") or "",
                   meta=_join("▲ %s" % _n(r.get("upvotes")), r.get("authors")))


def _newrepos(rows):
    for r in rows:
        yield dict(title=r.get("full_name"), url=r.get("url"), hn_url=None,
                   desc=r.get("description") or "",
                   meta=_join("★ %s" % _n(r.get("stars")), r.get("language"),
                              "created %s" % (r.get("created_at") or "")[:10]))


def _feeds(rows):
    for r in rows:
        yield dict(title=r.get("title"), url=r.get("url"), hn_url=None,
                   desc=(r.get("summary") or "")[:300],
                   meta=_join(r.get("feed"), (r.get("published") or "")[:10]))


def _reddit(rows):
    for r in rows:
        counts = []
        if r.get("score") is not None:
            counts.append("%s pts" % _n(r["score"]))
        if r.get("comments") is not None:
            counts.append("%s comments" % _n(r["comments"]))
        yield dict(title=r.get("title"), url=r.get("url") or r.get("permalink"), hn_url=None,
                   desc="",
                   meta=_join("r/%s" % r.get("subreddit"), *counts))


_ROWS = {"trending": _trending, "hackernews": _hn, "hnbest": _hn, "huggingface": _huggingface,
         "newrepos": _newrepos, "feeds": _feeds, "reddit": _reddit}


def normalise(day):
    items = {}
    for key in KEYS:
        if key not in day:
            continue
        for r in _ROWS[key](day[key]):
            url = (r["url"] or "").strip()
            if url:
                iid = item_id(url)
                slot = iid
            else:
                print("curate: %s row without url kept: %s" % (key, (r["title"] or "")[:60]),
                      file=sys.stderr)
                iid = item_id_for_text(key, r["title"] or "")
                slot = (iid, len(items))         # never merged with anything
                taken = {i["id"] for i in items.values()}
                base, n = iid, 2
                while iid in taken:
                    iid, n = "%s-%d" % (base, n), n + 1
                url = r["hn_url"] or ""
            it = items.get(slot)
            if it is None:
                it = items[slot] = dict(id=iid, sources=[], title="", url=url, meta="", hn_url=None,
                                         desc="")
            if key not in it["sources"]:
                it["sources"].append(key)
            it["title"] = it["title"] or (r["title"] or "")
            it["hn_url"] = it["hn_url"] or r["hn_url"]
            it["desc"] = it["desc"] or (r["desc"] if isinstance(r["desc"], str) else "").strip()
            it["meta"] = _join(it["meta"], r["meta"])
    return list(items.values())


_STR = {"type": "string"}
_IDS = {"type": "array", "items": _STR}
GIST_SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "required": ["id", "gist", "tags"],
                               "properties": {"id": _STR, "gist": _STR, "tags": _IDS}}}}}
THEMES_SCHEMA = {"type": "object", "required": ["themes", "rest"], "properties": {
    "themes": {"type": "array", "items": {"type": "object",
                                          "required": ["title", "summary", "item_ids"],
                                          "properties": {"title": _STR, "summary": _STR,
                                                         "item_ids": _IDS}}},
    "rest": _IDS}}


def _prompt(name, payload):
    with open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as f:
        return f.read().rstrip() + "\n\n" + json.dumps(payload, ensure_ascii=False)


def _ask_twice(ask, model, prompt, timeout, schema, key, stage):
    """One retry; returns the dict only if result[key] is a non-empty list."""
    for _ in range(2):
        try:
            out = ask(model, prompt, timeout, schema=schema)
        except Exception as e:
            print("curate: %s raised: %s" % (stage, e), file=sys.stderr)
            out = None
        got = out.get(key) if isinstance(out, dict) else None
        if isinstance(got, list) and (got or key != "items"):     # empty gist list = failure
            return out
    return None


def _clean_gist(it, got):
    g = got.get("gist") if isinstance(got, dict) else None
    g = g.strip() if isinstance(g, str) else ""
    if not g:
        return dict(it, gist=it["title"], tags=[])
    if len(g) > 140:
        g = g[:137] + "..."
    tags = got.get("tags") if isinstance(got.get("tags"), list) else []
    tags = [t.strip().lower() for t in tags if isinstance(t, str) and t.strip()][:3]
    return dict(it, gist=g, tags=tags)


def gists(items, ask, batch_size=40, workers=3, timeout=120):
    """Pass 1. Returns copies of items with gist and tags; titles stand in for failures."""
    def one(batch):
        payload = [{k: it[k] for k in ("id", "title", "url", "meta", "desc", "sources")}
                   for it in batch]
        out = _ask_twice(ask, "haiku", _prompt("gist.md", payload), timeout, GIST_SCHEMA, "items",
                         "gists")
        got = {g["id"]: g for g in (out or {}).get("items", [])
               if isinstance(g, dict) and isinstance(g.get("id"), str)}
        return [_clean_gist(it, got.get(it["id"])) for it in batch]
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [it for res in pool.map(one, batches) for it in res]


def themes(items, ask, timeout=300):
    """Pass 2. Raw model grouping plus grouped=True, or everything in rest on failure."""
    payload = [{k: it[k] for k in ("id", "gist", "tags", "sources")} for it in items]
    out = _ask_twice(ask, "sonnet", _prompt("themes.md", payload), timeout, THEMES_SCHEMA, "themes",
                     "themes")
    if out is None:
        return {"themes": [], "rest": [it["id"] for it in items], "grouped": False}
    return dict(out, grouped=True)


def _list(v):
    return v if isinstance(v, list) else []


def enforce(items, result):
    """Place every item id exactly once across themes and rest, repairing the model output."""
    result = result if isinstance(result, dict) else {}
    known = {it["id"]: it for it in items}
    seen, raw = set(), []

    def take(ids):
        kept = []
        for i in _list(ids):
            if isinstance(i, str) and i in known and i not in seen:
                seen.add(i)
                kept.append(i)
        return kept

    for t in _list(result.get("themes")):
        if isinstance(t, dict):
            raw.append((t, take(t.get("item_ids"))))
    rest = take(result.get("rest"))
    kept = []
    for n, (t, ids) in enumerate(raw):
        if len(ids) < 2:
            rest.extend(ids)
            continue
        title = str(t.get("title") if t.get("title") is not None else "").strip() or "Untitled"
        summary = str(t.get("summary") if t.get("summary") is not None else "").strip()
        srcs = {s for i in ids for s in known[i]["sources"]}
        kept.append(((-len(srcs), -len(ids), n), dict(title=title, summary=summary, item_ids=ids)))
    rest.extend(it["id"] for it in items if it["id"] not in seen)
    out = [t for _, t in sorted(kept, key=lambda x: x[0])]
    return {"themes": out, "rest": rest, "grouped": bool(result.get("grouped")) and bool(out)}


def _placed(r):
    return [i for t in r["themes"] for i in t["item_ids"]] + list(r["rest"])


def placement_ok(out):
    """Belt to enforce's braces: every item id placed exactly once."""
    placed = _placed(out)
    return len(placed) == len(out["items"]) and set(placed) == set(out["items"])


CURATE_BUDGET = 1400      # seconds; daily_brief.sh kills curate at CURATE_TIMEOUT=1500
THEMES_RESERVE = 650      # themes worst case is 2 x 300 s; skip it when less than this is left


def _write(path, out):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def _result_doc(date, day, items, r, probe_ok, themes_skipped):
    placed = _placed(r)
    return dict(date=date, items={it["id"]: it for it in items}, themes=r["themes"], rest=r["rest"],
                stats=dict(items_in=len(items), items_placed=len(placed),
                           sources=[k for k in KEYS if k in day], grouped=r["grouped"],
                           probe_ok=probe_ok, themes_skipped=themes_skipped))


def run(date, sent_dir, tmp_dir, ask=claude_cli.ask, probe=claude_cli.probe, no_model=False,
        now=time.monotonic):
    """Load, gist, theme, enforce; write <sent_dir>/<date>-curated.json and return it."""
    t0 = now()
    deadline = t0 + CURATE_BUDGET
    os.makedirs(tmp_dir, exist_ok=True)
    path = os.path.join(sent_dir, "%s-curated.json" % date)
    day = load_day(sent_dir, date)
    items = normalise(day)
    plain = [dict(it, gist=it["title"], tags=[]) for it in items]
    ungrouped = {"themes": [], "rest": [it["id"] for it in items], "grouped": False}
    # Titles-only fallback first, so a hard kill during the model passes still leaves a sendable file.
    _write(path, _result_doc(date, day, plain, enforce(plain, ungrouped), None, False))
    probe_ok, themes_skipped = None, False
    if not no_model:
        try:
            probe_ok = bool(probe())
        except Exception as e:
            print("curate: probe raised: %s" % e, file=sys.stderr)
            probe_ok = False
    if probe_ok:
        items = gists(items, ask)
        if deadline - now() < THEMES_RESERVE:
            print("curate: themes skipped, out of time", file=sys.stderr)
            themes_skipped = True
            result = {"themes": [], "rest": [it["id"] for it in items], "grouped": False}
        else:
            result = themes(items, ask)
    else:
        items, result = plain, ungrouped
    out = _result_doc(date, day, items, enforce(items, result), probe_ok, themes_skipped)
    _write(path, out)
    return out


def main(argv):
    args = [a for a in argv if a != "--no-model"]
    if len(args) != 3 or any(a.startswith("--") for a in args):
        print("usage: curate.py <date> <sent_dir> <tmp_dir> [--no-model]", file=sys.stderr)
        return 1
    out = run(*args, no_model="--no-model" in argv)
    st = out["stats"]
    probe = {None: "skipped", True: "ok", False: "failed"}[st["probe_ok"]]
    print("curate: %d items, %d themes, grouped=%d, probe=%s"
          % (st["items_in"], len(out["themes"]), st["grouped"], probe))
    if not placement_ok(out):
        print("curate: invariant broken: %d in, %d placed, ids differ or repeat"
              % (st["items_in"], st["items_placed"]), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

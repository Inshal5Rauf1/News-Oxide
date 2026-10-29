#!/usr/bin/env python3
"""
curate.py: first half of the curate stage (no LLM).
  load_day     read the archived sent/<date>-<key>.json snapshots
  normalise    flatten them into one item list, merging items with the same URL
Item: {id, sources, title, url, meta, hn_url}. The model passes come later.
"""
import sys, hashlib, json, os, urllib.parse

KEYS = ["trending", "hackernews", "hnbest", "huggingface", "newrepos", "feeds", "reddit"]
PERIODS = ["daily", "weekly", "monthly", "yearly"]
DROP_PARAMS = {"ref", "ref_src", "fbclid", "gclid"}


def canonical_url(url):
    p = urllib.parse.urlsplit((url or "").strip())
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
        yield dict(title=r.get("title"), url=r.get("url"), hn_url=r.get("hn_url"),
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
                   meta=_join("★ %s ▲ %s" % (_n(r.get("stars")), _n(r.get("stars_gained"))),
                              r.get("language"), ", ".join(e["periods"])))


def _huggingface(d):
    for kind in ("models", "datasets", "spaces"):
        for r in d.get(kind) or []:
            yield dict(title=r.get("id"), url=r.get("url"), hn_url=None,
                       meta=_join("♥ %s" % _n(r.get("likes")),
                                  "↓ %s" % _n(r["downloads"]) if r.get("downloads") is not None else "",
                                  r.get("pipeline_tag") or r.get("sdk") or kind[:-1]))
    for r in d.get("papers") or []:
        yield dict(title=r.get("title"), url=r.get("url"), hn_url=None,
                   meta=_join("▲ %s" % _n(r.get("upvotes")), r.get("authors")))


def _newrepos(rows):
    for r in rows:
        yield dict(title=r.get("full_name"), url=r.get("url"), hn_url=None,
                   meta=_join("★ %s" % _n(r.get("stars")), r.get("language"),
                              "created %s" % (r.get("created_at") or "")[:10]))


def _feeds(rows):
    for r in rows:
        yield dict(title=r.get("title"), url=r.get("url"), hn_url=None,
                   meta=_join(r.get("feed"), (r.get("published") or "")[:10]))


def _reddit(rows):
    for r in rows:
        counts = []
        if r.get("score") is not None:
            counts.append("%s pts" % _n(r["score"]))
        if r.get("comments") is not None:
            counts.append("%s comments" % _n(r["comments"]))
        yield dict(title=r.get("title"), url=r.get("url") or r.get("permalink"), hn_url=None,
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
                it = items[slot] = dict(id=iid, sources=[], title="", url=url, meta="", hn_url=None)
            if key not in it["sources"]:
                it["sources"].append(key)
            it["title"] = it["title"] or (r["title"] or "")
            it["hn_url"] = it["hn_url"] or r["hn_url"]
            it["meta"] = _join(it["meta"], r["meta"])
    return list(items.values())


def main():
    print("usage: curate.py (CLI arrives in a later task; import load_day / normalise)")


if __name__ == "__main__":
    main()

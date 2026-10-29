#!/usr/bin/env python3
"""
feedparse.py: stdlib RSS 2.0 / Atom / OPML parsing (no network, no deps).
  parse_feed(text)  -> [{title, url, published, id, summary}]  ([] on any failure)
  read_opml(path)   -> [{title, xmlUrl}]
  parse_date(s)     -> tz-aware UTC datetime or None
"""
import re, html
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def parse_date(s):
    """RFC 822 or ISO 8601 -> tz-aware UTC datetime, else None."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _name(el):
    return el.tag.split("}")[-1] if isinstance(el.tag, str) else ""


def _text(el, *names):
    for c in el:
        if _name(c) in names and (c.text or "").strip():
            return c.text.strip()
    return ""


def _plain(s):
    return re.sub(r"\s+", " ", re.sub("<[^>]+>", "", html.unescape(s))).strip()


def _link(el):
    """RSS <link>text</link>, or Atom <link rel=alternate href>."""
    href = ""
    for c in el:
        if _name(c) != "link":
            continue
        if c.get("href"):
            if c.get("rel", "alternate") == "alternate":
                return c.get("href").strip()
            href = href or c.get("href").strip()
        elif (c.text or "").strip():
            return c.text.strip()
    return href


def _item(el):
    d = parse_date(_text(el, "pubDate", "published", "updated", "date"))
    url = _link(el)
    return {
        "title": _text(el, "title"),
        "url": url,
        "published": d.isoformat() if d else "",
        "id": _text(el, "guid", "id") or url,
        "summary": _plain(_text(el, "description", "summary", "content", "encoded"))[:300],
    }


def parse_feed(text):
    try:
        root = ET.fromstring(text)
        return [_item(e) for e in root.iter() if _name(e) in ("item", "entry")]
    except Exception:
        return []


def read_opml(path):
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return []
    return [{"title": o.get("title") or o.get("text") or "", "xmlUrl": o.get("xmlUrl")}
            for o in root.iter("outline") if o.get("xmlUrl")]

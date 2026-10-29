#!/usr/bin/env python3
"""
render_email.py — snapshot-brief renderer + MIME builder.

Subcommands:
  trending|huggingface|hackernews <data_json> <date> <out_html>   render a snapshot email
  heartbeat <date> <out_html>                                     all-empty heartbeat HTML
  eml <subject> <html_file> <to_csv> <out_path>                   write RFC822 for gmail upload

Design "dark tech": GitHub-dark palette, monospace labels, neon accents.
Email-client hardened: table-based layout, inline styles, explicit bgcolor.
"""
import os
import sys, json, html
from email.mime.text import MIMEText

# ---- palette -----------------------------------------------------------------
BG      = "#0d1117"
PANEL   = "#161b22"
BORDER  = "#21262d"
FG      = "#f0f6fc"
MUTED   = "#8b949e"
LINK    = "#58a6ff"
GREEN   = "#39d353"
MONO    = "'SF Mono','Menlo','Consolas','Liberation Mono',monospace"
SANS    = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def esc(s):
    return html.escape(str(s if s is not None else ""))


def pill(text, fg, bg, bd):
    return (f'<span style="font-family:{MONO};font-size:10px;font-weight:700;color:{fg};'
            f'background:{bg};border:1px solid {bd};padding:2px 7px;border-radius:4px;'
            f'display:inline-block;">{esc(text)}</span>')


def group_header(text):
    return f'''
    <tr><td style="padding:18px 0 4px 0;">
      <span style="font-family:{MONO};font-size:12px;font-weight:700;color:{GREEN};letter-spacing:1px;">{esc(text)}</span>
      <div style="height:1px;background:{BORDER};margin-top:6px;"></div>
    </td></tr>'''


def _doc(header, inner, footer):
    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{BG}" style="background:{BG};">
<tr><td align="center" style="padding:20px 12px;">
  <table role="presentation" width="640" cellpadding="0" cellspacing="0" bgcolor="{BG}"
         style="width:640px;max-width:100%;background:{BG};border:1px solid {BORDER};border-radius:12px;">
    <tr><td style="padding:20px 22px;border-bottom:1px solid {BORDER};">{header}</td></tr>
    <tr><td style="padding:0 22px 8px 22px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{inner}</table>
    </td></tr>
    <tr><td style="padding:16px 22px;border-top:1px solid {BORDER};">
      <div style="font-family:{MONO};font-size:10px;color:{MUTED};">{esc(footer)}</div>
    </td></tr>
  </table>
</td></tr></table></body></html>'''


def _hdr(cmd, title, date, sub=""):
    s = f'<div style="font-family:{MONO};font-size:11px;color:{MUTED};margin-top:2px;">{esc(sub)}</div>' if sub else ""
    return (f'<div style="font-family:{MONO};font-size:12px;color:{GREEN};">~/daily-brief --{esc(cmd)} --{esc(date)}</div>'
            f'<div style="font-family:{SANS};font-size:22px;font-weight:700;color:{FG};margin-top:4px;">{esc(title)}</div>{s}')


def _meta(text):
    return f'<span style="color:{MUTED};font-family:{MONO};font-size:11px;">{text}</span>'


def _gray_pill(text):
    return pill(text, MUTED, "rgba(139,148,158,0.12)", "rgba(139,148,158,0.4)")


# ---- GitHub Trending ---------------------------------------------------------
def render_repo_row(r):
    name = esc(r.get("full_name", ""))
    url = esc(r.get("url", "#"))
    rank = esc(r.get("rank", ""))
    lang = r.get("language") or ""
    stars, gained = r.get("stars"), r.get("stars_gained")
    star_txt = ""
    if isinstance(stars, int):
        star_txt = f'<span style="color:{MUTED};font-family:{MONO};font-size:11px;">&#9733; {stars:,}</span>'
        if isinstance(gained, int) and gained:
            star_txt += f' <span style="color:{GREEN};font-family:{MONO};font-size:11px;">&#9650;{gained:,}</span>'
    lang_pill = pill(lang, MUTED, "rgba(139,148,158,0.12)", "rgba(139,148,158,0.4)") if lang else ""
    tags = " ".join(f'<span style="color:{MUTED};font-size:10px;">#{esc(t)}</span>' for t in (r.get("tags") or []))
    return f'''
    <tr><td style="padding:9px 0;border-bottom:1px solid {BORDER};">
      <div><span style="color:{MUTED};font-family:{MONO};font-size:11px;">#{rank}</span>
        <a href="{url}" style="color:{LINK};text-decoration:none;font-weight:700;font-size:14px;font-family:{SANS};">{name}</a>
        &nbsp;{lang_pill} &nbsp;{star_txt}</div>
      <div style="color:#c9d1d9;font-size:12px;margin-top:2px;font-family:{SANS};">{esc(r.get("description",""))}</div>
      {f'<div style="margin-top:3px;">{tags}</div>' if tags else ''}
    </td></tr>'''


def render_trending(data, date):
    periods = [("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"), ("yearly", "Yearly")]
    rows = []
    for key, label in periods:
        repos = data.get(key, []) or []
        rows.append(group_header(f"{label.upper()} TRENDING ({len(repos)})"))
        rows += [render_repo_row(r) for r in repos if r.get("full_name")]
    total = sum(len(data.get(k, []) or []) for k, _ in periods)
    hdr = _hdr("github-trending", "GitHub Trending", date, "Trendshift mirror — daily · weekly · monthly · yearly")
    return _doc(hdr, "".join(rows), f"{total} repos · source: trendshift.io · saved locally")


# ---- Hugging Face ------------------------------------------------------------
def render_huggingface(data, date):
    rows = []
    secs = [("models", "Trending Models"), ("datasets", "Trending Datasets"),
            ("spaces", "Trending Spaces"), ("papers", "Daily Papers")]
    for key, label in secs:
        items = data.get(key, []) or []
        rows.append(group_header(f"{label.upper()} ({len(items)})"))
        for it in items:
            if "error" in it:
                continue
            if key == "papers":
                name, url = it.get("title", ""), it.get("url", "#")
                up = it.get("upvotes")
                meta = _meta(f"&#9650;{up}") if isinstance(up, int) else ""
                extra = f' <span style="color:{MUTED};font-size:11px;">{esc(it.get("authors",""))}</span>' if it.get("authors") else ""
            else:
                name, url = it.get("id", ""), it.get("url", "#")
                bits = []
                if isinstance(it.get("likes"), int):
                    bits.append(f"&#9829; {it['likes']:,}")
                if isinstance(it.get("downloads"), int):
                    bits.append(f"&#8595; {it['downloads']:,}")
                meta = _meta(" &middot; ".join(bits))
                tag = it.get("pipeline_tag") or it.get("sdk") or ""
                extra = f' {_gray_pill(tag)}' if tag else ""
            rows.append(f'<tr><td style="padding:7px 0;border-bottom:1px solid {BORDER};">'
                        f'<a href="{esc(url)}" style="color:{LINK};text-decoration:none;font-weight:600;font-size:13.5px;font-family:{SANS};">{esc(name)}</a> {meta}{extra}</td></tr>')
    total = sum(len(data.get(k, []) or []) for k, _ in secs)
    return _doc(_hdr("huggingface", "Hugging Face", date, "trending models &middot; datasets &middot; spaces &middot; daily papers"),
                "".join(rows), f"{total} items · source: huggingface.co · saved locally")


# ---- Hacker News -------------------------------------------------------------
def render_hackernews(data, date):
    rows = [group_header(f"TOP FRONT-PAGE STORIES ({len(data)})")]
    for i, h in enumerate(data, 1):
        if "error" in h:
            continue
        title, url = esc(h.get("title", "")), esc(h.get("url", "#"))
        hn = esc(h.get("hn_url", "#"))
        meta = (f'{_meta(str(h.get("points") or 0)+" pts")} &middot; '
                f'<a href="{hn}" style="color:{MUTED};font-family:{MONO};font-size:11px;text-decoration:none;">{h.get("comments") or 0} comments</a>')
        rows.append(f'<tr><td style="padding:8px 0;border-bottom:1px solid {BORDER};">'
                    f'<span style="color:{MUTED};font-family:{MONO};font-size:11px;">#{i}</span> '
                    f'<a href="{url}" style="color:{LINK};text-decoration:none;font-weight:600;font-size:14px;font-family:{SANS};">{title}</a>'
                    f'<div style="margin-top:2px;">{meta}</div></td></tr>')
    return _doc(_hdr("hacker-news", "Hacker News", date, "top front-page stories — all topics"),
                "".join(rows), f"{len(data)} stories · source: news.ycombinator.com")


# ---- heartbeat (all three snapshots empty) -----------------------------------
def render_heartbeat(date):
    msg = ("All snapshot sources returned nothing this cycle. The brief ran "
           "successfully &mdash; there was simply nothing to report.")
    inner = (f'<tr><td style="padding:18px 0;color:#c9d1d9;font-size:14px;line-height:1.6;font-family:{SANS};">'
             f'{msg}</td></tr>')
    header = (f'<div style="font-family:{MONO};font-size:12px;color:{GREEN};">'
              f'~/daily-brief --heartbeat --{esc(date)}</div>'
              f'<div style="font-family:{SANS};font-size:22px;font-weight:700;color:{FG};margin-top:4px;">'
              f'Nothing to report today</div>')
    return _doc(header, inner, "snapshot brief · ran clean")


# ---- MIME --------------------------------------------------------------------
def build_eml(subject, html_file, to_csv, out_path):
    """Write the raw RFC822 message to a file for Gmail media upload (no arg-size limit)."""
    with open(html_file, encoding="utf-8") as f:
        html_body = f.read()
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = os.environ.get("FROM_ADDR", "daily-brief@localhost")
    msg["To"] = to_csv
    raw = msg.as_bytes()
    with open(out_path, "wb") as f:
        f.write(raw)
    if len(raw) > 100_000:   # Gmail clips ~102KB on desktop → bounces reader to browser
        sys.stderr.write(f"WARN: {out_path} {len(raw)} bytes (>100KB Gmail clip risk)\n")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: render_email.py trending|huggingface|hackernews|heartbeat|eml ...")
    cmd = sys.argv[1]
    if cmd in ("trending", "huggingface", "hackernews"):
        items_json, date, out_html = sys.argv[2:5]
        with open(items_json, encoding="utf-8") as f:
            data = json.load(f)
        fn = {"trending": render_trending, "huggingface": render_huggingface, "hackernews": render_hackernews}[cmd]
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(fn(data, date))
    elif cmd == "heartbeat":
        date, out_html = sys.argv[2:4]
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(render_heartbeat(date))
    elif cmd == "eml":
        subject, html_file, to_csv, out_path = sys.argv[2:6]
        build_eml(subject, html_file, to_csv, out_path)
    else:
        sys.exit(f"unknown subcommand: {cmd}")


if __name__ == "__main__":
    main()

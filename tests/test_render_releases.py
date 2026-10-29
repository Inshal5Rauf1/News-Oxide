import email
import email.header
import json
import os
import sys
from pathlib import Path

import pytest

import render_email as re_

FIXTURES = Path(__file__).parent / "fixtures"
EML_SMALL = FIXTURES / "eml_small.html"
EML_BEFORE = FIXTURES / "eml_before.eml"

RELEASES = [
    {"repo": "owner/alpha", "tag": "v1.2.0", "name": "Alpha 1.2",
     "url": "https://github.com/owner/alpha/releases/tag/v1.2.0",
     "published": "2026-10-28T09:15:00Z", "notes": "Faster parser & new CLI flag."},
    {"repo": "owner/beta", "tag": "v0.9.1", "name": "Beta 0.9.1",
     "url": "https://github.com/owner/beta/releases/tag/v0.9.1",
     "published": "2026-10-27T18:00:00Z", "notes": "Bug fixes."},
]


def test_build_eml_without_attachment_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("FROM_ADDR", raising=False)
    out = tmp_path / "out.eml"
    re_.build_eml("Daily Brief · Test · Oct 30", str(EML_SMALL),
                  "a@example.com,b@example.com", str(out))
    assert out.read_bytes() == EML_BEFORE.read_bytes()


def test_build_eml_with_attachment_is_multipart(tmp_path, monkeypatch):
    monkeypatch.setenv("FROM_ADDR", "brief@example.com")
    attach = tmp_path / "curated-2026-10-29.html"
    attach.write_text("<html><body>curated</body></html>", encoding="utf-8")
    out = tmp_path / "out.eml"
    re_.build_eml("Daily Brief · Test · Oct 30", str(EML_SMALL),
                  "a@example.com", str(out), attach=str(attach))

    msg = email.message_from_bytes(out.read_bytes())
    assert msg.get_content_type() == "multipart/mixed"
    subject = str(email.header.make_header(email.header.decode_header(msg["Subject"])))
    assert subject == "Daily Brief · Test · Oct 30"
    assert msg["From"] == "brief@example.com"
    assert msg["To"] == "a@example.com"
    parts = msg.get_payload()
    assert len(parts) == 2
    assert parts[0].get_content_type() == "text/html"
    assert parts[1].get_content_type() == "text/html"
    assert parts[1]["Content-Disposition"] == 'attachment; filename="curated-2026-10-29.html"'
    assert parts[1].get_payload(decode=True).decode("utf-8") == "<html><body>curated</body></html>"


def test_render_releases_rows():
    html = re_.render_releases(RELEASES, "2026-10-30")
    assert "NEW RELEASES (2)" in html
    assert "new releases of tools you use" in html
    assert "2 releases · source: github.com releases.atom" in html
    for r in RELEASES:
        assert f'href="{r["url"]}"' in html
        assert f">{r['repo']}</a>" in html
        assert re_.esc(r["tag"]) in html
        assert r["published"][:10] in html
        assert re_.esc(r["notes"]) in html
    assert "2026-10-28T09" not in html


def test_render_releases_empty_group_header_count():
    html = re_.render_releases([], "2026-10-30")
    assert "NEW RELEASES (0)" in html
    assert "0 releases" in html


def test_no_em_dash_in_any_renderer():
    trending = {
        "daily": [{"full_name": "o/r", "url": "https://x", "rank": 1, "stars": 5,
                   "description": "d", "tags": ["t"]}],
        "weekly": [], "monthly": [], "yearly": [],
    }
    huggingface = {
        "models": [{"id": "m/x", "url": "https://hf/m", "likes": 3, "downloads": 9}],
        "datasets": [], "spaces": [],
        "papers": [{"title": "P", "url": "https://hf/p", "upvotes": 2, "authors": "A"}],
    }
    hackernews = [{"title": "T", "url": "https://t", "hn_url": "https://hn",
                   "points": 4, "comments": 2}]
    renders = {
        "trending": re_.render_trending(trending, "2026-10-30"),
        "huggingface": re_.render_huggingface(huggingface, "2026-10-30"),
        "hackernews": re_.render_hackernews(hackernews, "2026-10-30"),
        "releases": re_.render_releases(RELEASES, "2026-10-30"),
        "heartbeat": re_.render_heartbeat("2026-10-30"),
    }
    for name, html in renders.items():
        assert "\u2014" not in html, f"em dash in {name} renderer"
        assert "&mdash;" not in html, f"em dash entity in {name} renderer"


def test_cli_releases_and_eml_attach(tmp_path, monkeypatch):
    data = tmp_path / "releases.json"
    data.write_text(json.dumps(RELEASES), encoding="utf-8")
    out_html = tmp_path / "releases.html"
    monkeypatch.setattr(sys, "argv", ["render_email.py", "releases",
                                      str(data), "2026-10-30", str(out_html)])
    re_.main()
    assert "NEW RELEASES (2)" in out_html.read_text(encoding="utf-8")

    attach = tmp_path / "curated-2026-10-30.html"
    attach.write_text("<p>curated</p>", encoding="utf-8")
    out_eml = tmp_path / "out.eml"
    monkeypatch.setattr(sys, "argv", ["render_email.py", "eml",
                                      "Subj", str(EML_SMALL), "a@example.com",
                                      str(out_eml), str(attach)])
    re_.main()
    msg = email.message_from_bytes(out_eml.read_bytes())
    assert msg.is_multipart()
    assert len(msg.get_payload()) == 2

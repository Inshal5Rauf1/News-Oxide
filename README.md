# News Oxide

A personal daily brief. Fetches GitHub Trending, Hugging Face, Hacker News, new
GitHub repos, releases of the tools you use, RSS feeds and subreddits, then
sends raw snapshot emails and one curated email that groups the day into
themes. Nothing is ever dropped; the model only groups and summarises.

Design: `docs/superpowers/specs/2026-10-29-daily-brief-v2-design.md`.
Runs from a systemd user timer at 12:00 PKT. Model calls go through the
Claude Code CLI (`claude -p`), no API key.

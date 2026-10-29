# News Oxide: operations

Everything runs from this folder. The systemd user timer `daily-brief.timer`
fires `daily_brief.sh` at 12:00 PKT and catches up after sleep.

## What goes out each day

| email | subject | when |
|---|---|---|
| GitHub Trending | `Daily Brief · GitHub Trending · <date>` | always (raw snapshot) |
| Hugging Face | `Daily Brief · Hugging Face · <date>` | always (raw snapshot) |
| Hacker News | `Daily Brief · Hacker News · <date>` | always (raw snapshot) |
| Releases | `Daily Brief · Releases · <date>` | only when a watched repo shipped something new |
| Curated | `Daily Brief · Curated · <date>` (+ ` · ungrouped` when the model stage was skipped) | always; full brief attached as `curated-<date>.html` |
| health | `Daily Brief · ⚠ health · <date>` | to HEALTH_TO only, when any topic failed |

Recipients, the health address and the From address live in `config.env`
(not in git). Copy `config.env.example` to start.

## Sources

- `trending`, `huggingface`, `hackernews`: as v1.
- `newrepos`: GitHub repos created in the last 7 days with 100+ stars.
- `hnbest`: HN stories from the last 24 hours by points (not just the front page).
- `feeds`: every feed in `feeds.opml`. Edit the file to add or remove a feed.
- `reddit`: every subreddit in `subreddits.txt`, one per line. Reddit rate-limits;
  a few subreddits failing on a day is normal and only logged.
- `releases`: every repo in `releases.txt` plus your starred repos, via their
  `releases.atom` feeds. First run seeds `state/releases.json` silently.

`newrepos`, `hnbest`, `feeds`, `reddit` are never emailed raw; they flow into the
curated email only. Their JSON is archived in `sent/` like everything else.

## The curated stage

`curate.py` reads every archived JSON for the day, merges items that share a
URL, then:

1. Haiku writes a one-line gist and up to three tags per item (batches of 40,
   three at a time).
2. Sonnet groups the gists into themes, ordered by how many sources covered them.
3. The code enforces that every item appears exactly once across themes and
   "Also today", whatever the model returned. The renderer checks it again.

Model calls go through the Claude Code CLI (`claude -p`) on the subscription;
there is no API key. A probe call runs first; if it fails, the email still
goes out, ungrouped, with titles as gists, and health reports `probe(failed)`.

Prompts are plain text in `prompts/gist.md` and `prompts/themes.md`.

## Running by hand

```
./daily_brief.sh                      # full run, honours guards (what the timer does)
./daily_brief.sh --dry-run            # fetch + render everything, send nothing, no model calls
./daily_brief.sh --only=curate        # re-run the model stage on today's archives and send
./daily_brief.sh --only=releases      # re-check releases now
./daily_brief.sh --only=feeds,reddit  # re-fetch two sources (archives only; no email)
python3 curate.py 2026-10-30 sent tmp # the model stage alone
python3 -m pytest -q                  # the offline test suite
```

`--only=` bypasses the per-topic guard. Without it, a topic that already sent
today is skipped (guard files in `guard/<date>/`).

## Files

- `sent/<date>-<key>.json|html`: the day's archives; `sent/<date>-curated.json|html|-body.html`.
- `state/releases.json`, `state/feeds_seen.json`: committed only after a successful send.
- `tmp/`: scratch, cleared per topic at fetch start.
- `cron.log`: every run; the first place to look.

## When something is wrong

- A health email names the topic: `feeds(error)`, `curate(error)`, `probe(failed)`,
  `releases(commit-failed)`. Read `cron.log` for the matching lines.
- Model stage misbehaving: `./daily_brief.sh --only=curate` re-runs it on the
  same archives and sends again.
- Timer status: `systemctl --user status daily-brief.timer`;
  pause with `systemctl --user disable --now daily-brief.timer`,
  resume with `systemctl --user enable --now daily-brief.timer`.

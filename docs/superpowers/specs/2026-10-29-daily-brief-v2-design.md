# Daily Brief v2: curated brief on top of the raw snapshots

Date: 2026-10-29. Status: implemented 2026-10-30 on branch v2-curated (see docs/OPERATIONS.md).

## Intent

The current Daily Brief sends three raw snapshot emails at noon PKT (GitHub
Trending, Hugging Face, Hacker News). It is reliable and deterministic, and it
stays exactly as it is.

It misses four things the owner named:

1. New repositories on GitHub before they are popular. Trending only shows
   repos that are already large.
2. Releases of tools the owner already uses (K3s, Odoo, Canvas, H5P, Materia,
   RPG-JS, and whatever they star).
3. Sources outside GitHub, HN and HF: vendor and AI-lab blogs, subreddits,
   HN "best".
4. Global tech news that never reaches the HN front page.

v2 adds those sources, then uses a model to organise everything into one
readable email. The model groups and summarises. It never drops an item.

## Goals

- Every item fetched on a day appears in the owner's inbox that day.
- One curated email a day that a person can read top to bottom in ten
  minutes and come away knowing what the day's stories were.
- One small releases email a day listing new releases of the owner's tools.
- Zero new paid services. Model calls go through the owner's Claude Code
  subscription via `claude -p` (verified working headless on this machine,
  2026-10-29, model `claude-haiku-5-5`).
- Same reliability posture as v1: per-topic guard, catch-up after sleep,
  health alert on any failure, raw data archived in `sent/`.

## Non-goals

- No filtering or ranking by the owner's interests. Ordering is by how many
  sources covered a theme, nothing else. No interests file.
- No X/Twitter and no YouTube in this version. Every project surveyed that
  scrapes them is the one that breaks. Can be added later as extra fetchers.
- No web UI, no database, no vector search. Flat JSON files, as today.
- No change to the three raw emails, their renderers, or their subjects.

## Anti-goals (what would make this a failure even if it runs)

- The curated email silently omits items. If the model's output loses an
  item, the pipeline must notice and append it to the "Also today" tail
  itself. The invariant is checked in code, not trusted to the prompt.
- A model outage kills the whole brief. The three raw emails must still go
  out when `claude -p` fails, and the owner gets a health alert.
- Gmail clipping hides half the email with no way to the rest.
- Cost surprise. No API key anywhere in the system.

## Sources

### Existing (unchanged)

`trending`, `huggingface`, `hackernews` from `fetch_sources.py`. Their JSON
snapshots are also fed into the curated step.

### New fetchers (added to `fetch_sources.py`, same `get()` helper, stdlib only)

| key | what | how |
|---|---|---|
| `newrepos` | GitHub repos created in the last 7 days with >= 100 stars | GitHub search API, `created:>=<date> stars:>=100`, sorted by stars, first 100. Unauthenticated limit is 10 req/min, one request suffices. If a `GITHUB_TOKEN` is present in the environment, use it; it is optional. |
| `releases` | New releases/tags since the last run for a tool list | Checked list = `releases.txt` (one `owner/repo` per line, builder seeds it with the owner's stack: k3s-io/k3s, odoo/odoo, instructure/canvas-lms, h5p/h5p-php-library, ucfopen/Materia, RPG-JS/RPG-JS, plus any the owner adds) merged with the owner's starred repos (`/users/Inshal5Rauf1/starred`, public, 100 per page, cap 300, 3 API calls). Each repo is read through its public `https://github.com/{r}/releases.atom` feed, which is not subject to the 60/hour API limit, so no token is needed. New = entry id not in `state/releases.json` (last seen ids per repo) AND published within the last 7 days, so a skipped day still catches up and an old untouched repo is never announced. **First run** (no state file): seed the state from everything seen and send nothing. State is written only after a successful send, so a failed send re-announces next day. |
| `feeds` | RSS/Atom items published in the last 26 hours | Feed list in `feeds.opml`. Parse with `xml.etree`, handle both RSS 2.0 and Atom. 26-hour window so a late catch-up run does not drop anything. Items with no date are included once: `state/feeds_seen.json` holds the URLs already sent for undated items (pruned after 30 days); dated items use the window only. A feed that fails to parse is a log line; all feeds failing is an `error`. Builder seeds the OPML with: OpenAI, Anthropic, Google DeepMind, Hugging Face blog, GitHub blog, GitHub changelog, Simon Willison, TLDR, Changelog News, Import AI, Ben's Bites, Ars Technica, The Verge, TechCrunch, Lobsters front page RSS. Owner edits the file to add or remove. |
| `reddit` | Hot posts from a subreddit list | `https://www.reddit.com/r/<sub>/hot.json?limit=25` with a descriptive User-Agent (Reddit blocks the default). Builder seeds: r/MachineLearning, r/LocalLLaMA, r/selfhosted, r/programming, r/devops, r/kubernetes. Owner edits the list. One subreddit failing (403/429) is a log line; all failing is an `error`. If Reddit turns out to block this ISP outright, the owner is told once and the topic is removed from `TOPIC_KEYS`, not left to alert daily. |
| `hnbest` | HN "best" (top-voted past day), not just front page | HN Algolia `search?tags=front_page` is what we have; add `search?tags=story&numericFilters=created_at_i>` for the last 24h sorted by points, first 50, deduplicated against `hackernews`. |

Each new fetcher is a plain `--only=`-able topic in `daily_brief.sh`, with its
own guard file and status, and its own raw JSON archived to
`sent/<date>-<key>.json`. Fetch failures in one topic do not block others.

### Not emailed raw

`newrepos`, `feeds`, `reddit`, `hnbest` are NOT sent as separate raw emails.
Their raw JSON is archived and they flow into the curated email only. The
owner did not ask for seven emails. `releases` gets its own small email
(below). This is the designer's call; it is easy to flip per topic later.

## The curated step

Runs after all fetchers finish (after the existing `wait`). Reads every
topic's JSON for today (the three old ones plus the four new ones, whichever
succeeded) and builds one normalised item list:

```
{id, source, title, url, meta}   # meta: stars/points/author/published, free text
```

`id` is a stable hash of the canonical URL. Items with the same canonical URL
across sources are merged into one item with several `source` tags (this is
URL-level dedup, deterministic, not the model's job). An HN story keeps both
its article URL (used for merging) and its comments URL (kept in `meta` and
rendered as a second link), so a merge with a `feeds` item never loses the
discussion.

### Pass 1, Haiku: one-line gist per item

- Batches of ~40 items per call, each call a single `claude -p` invocation
  with `--model haiku --output-format json --no-session-persistence
  --tools "" --strict-mcp-config` and an empty MCP config so nothing
  else loads.
- Input: the item list for the batch, as JSON in the prompt. Output: JSON
  mapping `id -> {gist, tags[]}`; gist is one sentence, at most 140
  characters, in plain English; tags are 1 to 3 short lowercase words.
- The pipeline validates the JSON. Any id missing from the output keeps its
  original title as its gist. A batch call that fails is retried once, then
  its items proceed with titles as gists. The brief never waits on a stuck
  batch: per-call timeout 120 s.
- Batches run through a pool of 3 at a time (not fully parallel, to stay
  under subscription rate limits; not serial, to fit the time budget).
- Before pass 1, a probe call (`claude -p "Reply OK" --model haiku`, 60 s
  timeout) checks that the CLI and the subscription token work. If the
  probe fails, both passes are skipped, the curated email goes out ungrouped
  with titles as gists, and the health alert names the probe. This keeps a
  dead token from burning forty minutes of retries.

### Pass 2, Sonnet: themes

- One `claude -p --model sonnet` call. Input: the full list of
  `{id, gist, tags, sources}` (no URLs, no titles, to keep it short). Output:
  JSON `{themes: [{title, summary, item_ids[]}], rest: [item_ids]}`.
- Prompt rules: every id appears exactly once across themes and rest; a theme
  needs at least 2 items; summary is at most 2 sentences and names what the
  items have in common; theme order is by number of distinct sources in the
  theme, then by item count. No judgement of importance, no interests.
- The pipeline enforces the invariant: ids that the model left out are
  appended to `rest`; ids that appear twice keep their first placement;
  ids that do not exist are discarded. The count "items in, items rendered"
  is logged and must match; if it does not, the run is marked `error` and
  the health alert fires, but the email still sends.
- If the Sonnet call fails (timeout 300 s, one retry), the curated email is
  still sent with a single section per source, items in fetch order, gists
  from pass 1 if available. Subject gains the suffix " · ungrouped".

### Output: "Daily Brief · Curated · <date>"

Rendered by a new `render_curated()` in `render_email.py`, same dark
GitHub-style document helpers as the other renderers.

- Top: counts line ("412 items from 7 sources, 23 themes").
- Each theme: title, two-line summary, then every item as one row:
  source pill(s), gist, link, meta.
- "Also today": every unthemed item, grouped by source, same row format.
- Bottom: a line naming the attachment.
- Attachment (owner's decision, 2026-10-29): the complete brief, every theme
  and every item, is always attached as `curated-<date>.html`. It opens in
  any browser on any device, for both recipients. No `file://` links
  anywhere in the email.
- Body size rule: the body is the themes section (title, summary, item
  count) plus item rows for as many themes as fit under 95 KB, in order.
  Themes whose rows did not fit show "N items, see attachment". On a small
  day everything fits and the attachment is a duplicate; that is fine.
- `build_eml()` grows an optional attachment argument (multipart/mixed
  wrapping the existing HTML part). The three raw emails keep calling it
  without one and are byte-for-byte unchanged.

### Output: "Daily Brief · Releases · <date>"

Rendered by `render_releases()`. One row per new release: repo, tag, date,
first 300 characters of the release notes, link. Sent only when there is at
least one new release; otherwise nothing (no heartbeat for this topic, the
curated email's counts line notes "0 releases"). Not passed through the
model.

## Scheduling and operations

- Same `daily-brief.timer` at 12:00 PKT, `Persistent=true`.
- Stage order: all fetchers in parallel, then the three raw emails and the
  releases email are sent, then the `curate` stage runs. The raw emails
  never wait on the model.
- Guards: fetch-only topics (`newrepos`, `feeds`, `reddit`, `hnbest`) mark
  done on a successful fetch (their JSON archived to `sent/`), since they
  never send. `curate` has its own `curate.done`, written after the curated
  email sends, so a catch-up run never sends a second curated email. If a
  source failed on the first run, the curated email is still once per day;
  the health alert says which source was missing.
- `--only=curate` bypasses the guard like every other `--only`, reads
  today's `sent/<date>-<key>.json` for every key that has one, re-runs both
  passes, and sends (or renders only, under `--dry-run`). This is the
  "the model did something odd, run it again" lever.
- `TimeoutStartSec` in the service rises from 900 to 2400. Inside the
  script the curate stage gets its own deadline of 1500 s via `timeout`, so
  a slow model cannot stop the health alert at the end from running.
- Must be verified under systemd, not just a shell: the plan includes a
  `systemctl --user start daily-brief.service` with `--dry-run --only=curate`
  wired in, confirming `claude -p` finds its token with the service's
  environment.
- Health alert covers the new topics, the probe, and the curate stage.
- `state/` directory (releases last-seen, feeds seen) is git-tracked so a
  fresh clone does not re-announce every release; `sent/` and `tmp/` stay
  ignored as today.
- All prompts live in `prompts/gist.md` and `prompts/themes.md` as plain
  text so the owner can edit the wording in chat.

## Constraints

- Python 3 stdlib only for fetch and render, as today. No pip packages.
- `claude` CLI at `~/.local/bin/claude`, invoked with an explicit absolute
  path and the `HOME`/`PATH` the script already exports. The script must
  not rely on an interactive shell.
- Recipients unchanged: the owner and
  the second recipient (both set in `config.env`) for Curated and Releases. Health
  alerts go to the owner only, as today.
- No git push without the owner's say; work on a branch the owner names.
- No em dashes in any rendered text, prompt, or code comment.

## Left to the builder

- Exact batch size for pass 1 and the JSON schema wording in the prompts.
- Canonical-URL rules for dedup (strip tracking params, trailing slash,
  `www.`).
- How gists are capped if the model ignores the 140-character limit
  (truncate with an ellipsis is fine).
- The CSS for the theme header and the "Also today" tail, matching the
  existing `group_header()` style.
- Whether `hnbest` is worth keeping once it is seen next to `hackernews`;
  ship it, measure overlap for a week, report.

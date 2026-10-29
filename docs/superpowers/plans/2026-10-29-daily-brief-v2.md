# Daily Brief v2 (News Oxide) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four new source fetchers, a releases email, and a model-curated email that groups every fetched item into themes and never drops one, on top of the unchanged three raw snapshot emails.

**Architecture:** Same shape as v1: `daily_brief.sh` orchestrates topics in parallel subshells, each topic is `fetch_sources.py <key>` -> JSON -> `render_email.py <key>`. v2 adds fetchers that only archive JSON (no raw email), a `releases` topic with its own small email, and a `curate` stage after the raw sends that normalises every JSON, runs two model passes through the Claude Code CLI (`claude_cli.py`), enforces the no-drop invariant in code, and renders one email with the full brief attached.

**Tech Stack:** bash, Python 3.14 stdlib only at runtime (`urllib`, `xml.etree`, `json`, `email`, `concurrent.futures`), pytest 9 for tests, `claude` CLI 2.1.x for model calls, `gws` for Gmail send, systemd user timer.

**Spec:** `docs/superpowers/specs/2026-10-29-daily-brief-v2-design.md`

## Global Constraints

- Python 3 stdlib only in runtime modules. Tests may use pytest.
- No API key anywhere. Model calls only via `/home/inshal-rauf/.local/bin/claude -p`.
- The three raw emails (`trending`, `huggingface`, `hackernews`) keep their renderers, subjects, and `build_eml` output byte-for-byte when called without an attachment.
- Every item fetched on a day appears in the curated email's attachment. Enforced in code, checked by test.
- No em dashes (U+2014) in any rendered text, prompt, code comment, or commit message. Existing em dashes in `render_email.py` strings are replaced in Task 9.
- Recipients, health address, From address come from `config.env` (untracked). Never commit an email address.
- Commits are local on branch `v2-curated`. Pushing that branch is allowed after each task's commit; never push `main`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Working directory for all commands: `/home/inshal-rauf/News Oxide`. Run tests with `python3 -m pytest -q`.

## Review Focus

1. Model output wrapped in ```` ```json ```` fences or with trailing prose: must still parse. Pinned in Task 6 (`test_parse_result_strips_fences`).
2. A feed URL that returns an HTML error page instead of XML: that feed is a log line, the others still render. Pinned in Task 1 (`test_parse_feed_html_garbage_returns_empty`) and Task 3 (`test_feeds_one_bad_feed_does_not_kill_run`).
3. URL variants of the same story (`http` vs `https`, `www.`, trailing slash, `utm_*`) must merge, but `github.com/a/b` and `github.com/a/b/releases/tag/v1` must not. Pinned in Task 7 (`test_canonical_url_variants`, `test_canonical_url_keeps_path`).
4. Pass 2 returns an id twice, an id that does not exist, and forgets one: rendered count still equals input count. Pinned in Task 8 (`test_enforce_invariant_repairs_model_output`).
5. The 95 KB body cut lands on a theme boundary, never mid-row, and the attachment always holds every item. Pinned in Task 10 (`test_body_cut_at_theme_boundary`, `test_attachment_has_every_item`).

---

## File structure

| file | responsibility | task |
|---|---|---|
| `feedparse.py` (new) | stdlib RSS 2.0 / Atom parser -> list of dicts; OPML reader | 1 |
| `fetch_sources.py` (modify) | add `newrepos`, `hnbest`, `feeds`, `reddit`, `releases`, `commit-state` | 2, 3, 4, 5 |
| `feeds.opml`, `subreddits.txt`, `releases.txt` (new) | owner-editable source lists | 3, 4, 5 |
| `state/` (new, tracked) | `releases.json`, `feeds_seen.json` | 3, 5 |
| `claude_cli.py` (new) | `ask()` wrapper around `claude -p`, `probe()` | 6 |
| `curate.py` (new) | normalise + dedup, pass 1, pass 2, invariant, write `sent/<date>-curated.json` | 7, 8 |
| `prompts/gist.md`, `prompts/themes.md` (new) | owner-editable prompt text | 8 |
| `render_email.py` (modify) | `render_releases`, `render_curated`, `build_eml(attach=)`, em dash purge | 9, 10 |
| `daily_brief.sh` (modify) | new topics, stage order, guards, curate stage, `--only=curate` | 11 |
| `~/.config/systemd/user/daily-brief.service` (modify) | ExecStart -> News Oxide, TimeoutStartSec=2400 | 12 |
| `tests/` (new) | pytest, fixtures under `tests/fixtures/` | all |

---

### Task 1: feedparse.py (RSS/Atom/OPML, stdlib)

**Files:**
- Create: `feedparse.py`, `tests/test_feedparse.py`, `tests/fixtures/rss2.xml`, `tests/fixtures/atom.xml`, `tests/fixtures/sample.opml`, `tests/fixtures/garbage.html`

**Interfaces:**
- Produces: `parse_feed(text: str) -> list[dict]` with keys `title, url, published (ISO 8601 str or ""), id, summary (plain text, first 300 chars)`. Returns `[]` on any parse failure, never raises.
- Produces: `read_opml(path: str) -> list[dict]` with keys `title, xmlUrl`.
- Produces: `parse_date(s: str) -> datetime | None` handling RFC 822 (`email.utils.parsedate_to_datetime`) and ISO 8601 (`datetime.fromisoformat`, `Z` accepted); always returns tz-aware UTC or None.

- [ ] **Step 1: Write fixtures.** `rss2.xml` with 3 items (one with CDATA title, one with `pubDate` in RFC 822, one with no date). `atom.xml` with 2 entries (`<link rel="alternate" href>`, `<published>` and `<updated>` ISO). `sample.opml` with 2 outlines inside a nested `<outline>` folder. `garbage.html` is any HTML page.
- [ ] **Step 2: Write failing tests** in `tests/test_feedparse.py`:
  - `test_parse_rss2_items`: 3 items; CDATA title decoded; the RFC 822 date becomes `published` ending in `+00:00`; undated item has `published == ""`.
  - `test_parse_atom_entries`: 2 items; `url` is the alternate link; `published` from `<published>`, falling back to `<updated>`.
  - `test_parse_feed_html_garbage_returns_empty`: `parse_feed(garbage) == []`.
  - `test_read_opml_nested`: 2 feeds found inside the folder.
  - `test_parse_date_variants`: RFC 822, ISO with `Z`, ISO with offset, junk -> None.
- [ ] **Step 3: Run** `python3 -m pytest tests/test_feedparse.py -q`. Expected: FAIL, import error.
- [ ] **Step 4: Implement** `feedparse.py`. Use `xml.etree.ElementTree.fromstring`; strip namespaces by matching on `tag.split('}')[-1]`; summary via `html.unescape` then `re.sub('<[^>]+>', '', ...)`.
- [ ] **Step 5: Run** the tests. Expected: 5 passed.
- [ ] **Step 6: Commit** `feat: stdlib RSS/Atom/OPML parser`.

### Task 2: newrepos and hnbest fetchers

**Files:**
- Modify: `fetch_sources.py` (dispatch dict at the bottom, add two functions)
- Test: `tests/test_fetch_simple.py`

**Interfaces:**
- Consumes: `get(url)` from `fetch_sources.py`. Tests monkeypatch `fetch_sources.get`.
- Produces: `newrepos()` prints a JSON list of `{full_name, url, description, language, stars, created_at}` (up to 100). Query: `https://api.github.com/search/repositories?q=created:>=<YYYY-MM-DD 7 days ago> stars:>=100&sort=stars&order=desc&per_page=100` (URL-encoded). If `GITHUB_TOKEN` is set in the environment, send `Authorization: Bearer`.
- Produces: `hnbest()` prints a JSON list in the same shape as `hackernews()` (`title, url, points, comments, hn_url, author`), from `https://hn.algolia.com/api/v1/search?tags=story&numericFilters=created_at_i><epoch 24h ago>&hitsPerPage=50`, sorted by points desc.

- [ ] **Step 1: Write failing tests**: `test_newrepos_shape` (monkeypatched `get` returns a canned search payload; assert the query string contains `created:>=` and `stars:>=100`, output has the six keys), `test_newrepos_failure_prints_empty_list` (get raises -> `[]`), `test_hnbest_shape_and_sort`.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement** both functions; each wraps its fetch in `try/except Exception: out = []` like `hackernews()`. Extend `get()` to accept `headers: dict | None = None` merged over `UA`, keeping the one-argument call working.
- [ ] **Step 4: Run** tests, expect PASS. Then a live smoke: `python3 fetch_sources.py newrepos | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d), d[0]['full_name'] if d else '')"`. Expected: a count > 0.
- [ ] **Step 5: Commit** `feat: newrepos and hnbest fetchers`.

### Task 3: feeds fetcher with OPML and seen-state

**Files:**
- Create: `feeds.opml`, `state/feeds_seen.json` (initial `{}`)
- Modify: `fetch_sources.py`
- Test: `tests/test_fetch_feeds.py`

**Interfaces:**
- Consumes: `feedparse.parse_feed`, `feedparse.read_opml`, `feedparse.parse_date`.
- Produces: `feeds()` prints a JSON list of `{title, url, feed (outline title), published, summary}`. Reads `feeds.opml` next to the script. Includes dated items with `published` within the last 26 hours; includes undated items whose `url` is not in `state/feeds_seen.json`; writes `tmp/feeds.state.json` = merged seen map `{url: first_seen_date}` pruned to 30 days (to be committed by Task 5's `commit-state`). Prints to stderr one line per feed that failed; exits 0 with `[]` only if every feed failed (the orchestrator treats `[]` as `empty`; "all failed" is reported by exit code 3 so the orchestrator can mark `error`).
- Module-level `FEEDS_OPML`, `STATE_DIR`, `TMP_DIR` paths derived from `__file__` so tests can monkeypatch them.

- [ ] **Step 1: Seed `feeds.opml`** with these outlines (title, xmlUrl): OpenAI News `https://openai.com/news/rss.xml`; Anthropic `https://www.anthropic.com/rss.xml`; Google DeepMind `https://deepmind.google/blog/rss.xml`; Hugging Face Blog `https://huggingface.co/blog/feed.xml`; GitHub Blog `https://github.blog/feed/`; GitHub Changelog `https://github.blog/changelog/feed/`; Simon Willison `https://simonwillison.net/atom/everything/`; TLDR `https://tldr.tech/api/rss/tech`; Changelog News `https://changelog.com/news/feed`; Import AI `https://importai.substack.com/feed`; Ben's Bites `https://www.bensbites.com/feed`; Ars Technica `https://feeds.arstechnica.com/arstechnica/technology-lab`; The Verge `https://www.theverge.com/rss/index.xml`; TechCrunch `https://techcrunch.com/feed/`; Lobsters `https://lobste.rs/rss`. The implementer verifies each URL returns XML with `curl -sI` and replaces any dead one with the site's advertised feed; note replacements in the commit body.
- [ ] **Step 2: Write failing tests** with `get` monkeypatched to serve fixture text per URL: `test_feeds_window_26h` (dated item 25 h old included, 27 h old excluded), `test_feeds_undated_sent_once` (undated item included on first run, excluded when present in seen state, state file written with it), `test_feeds_one_bad_feed_does_not_kill_run` (one URL returns `garbage.html`, other items still present, stderr has one line), `test_feeds_all_failed_exit_3`.
- [ ] **Step 3: Run**, expect FAIL.
- [ ] **Step 4: Implement** `feeds()`.
- [ ] **Step 5: Run** tests, expect PASS. Live smoke: `python3 fetch_sources.py feeds | python3 -c "import json,sys;print(len(json.load(sys.stdin)))"` > 0.
- [ ] **Step 6: Commit** `feat: feeds fetcher with OPML list and seen-state`.

### Task 4: reddit fetcher

**Files:**
- Create: `subreddits.txt` (one per line: `MachineLearning`, `LocalLLaMA`, `selfhosted`, `programming`, `devops`, `kubernetes`)
- Modify: `fetch_sources.py`
- Test: `tests/test_fetch_reddit.py`

**Interfaces:**
- Produces: `reddit()` prints a JSON list of `{title, url, subreddit, score, comments, permalink}` from `https://www.reddit.com/r/<sub>/hot.json?limit=25` with header `User-Agent: news-oxide/1.0 (personal daily brief; by u/<owner>)`. Stickied posts skipped. `url` is the post's external link or the permalink for self posts; `permalink` is always the full reddit URL. One subreddit failing -> stderr line; all failing -> exit 3.

- [ ] **Step 1: Write failing tests**: `test_reddit_shape_skips_stickied`, `test_reddit_one_sub_fails_others_survive`, `test_reddit_all_fail_exit_3`, `test_reddit_user_agent_header` (monkeypatched `get` captures headers).
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement** `reddit()`.
- [ ] **Step 4: Run** tests, expect PASS. Live smoke: run `python3 fetch_sources.py reddit`; record in the commit body whether this ISP gets 200 or 403/429 (the design says: if blocked outright, tell the owner once and drop the topic from `TOPIC_KEYS`; do not do that silently).
- [ ] **Step 5: Commit** `feat: reddit fetcher`.

### Task 5: releases fetcher, starred repos, state commit

**Files:**
- Create: `releases.txt` (`k3s-io/k3s`, `odoo/odoo`, `instructure/canvas-lms`, `h5p/h5p-php-library`, `ucfopen/Materia`, `RPG-JS/RPG-JS`), `state/releases.json` (absent until first run seeds it)
- Modify: `fetch_sources.py`
- Test: `tests/test_fetch_releases.py`

**Interfaces:**
- Consumes: `feedparse.parse_feed` on `https://github.com/<owner>/<repo>/releases.atom`.
- Produces: `releases()` prints a JSON list of `{repo, tag, name, url, published, notes (summary, 300 chars)}`. Checked list = `releases.txt` union starred repos from `https://api.github.com/users/<GITHUB_USER>/starred?per_page=100&page=1..3` (`GITHUB_USER` from env, default `Inshal5Rauf1`; if the starred call fails, continue with `releases.txt` only and print a stderr line). New = atom entry `id` not in `state/releases.json[repo]` AND `published` within 7 days. Writes `tmp/releases.state.json` = `{repo: [ids seen, capped at 20 newest]}`. **First run** (`state/releases.json` missing): write the state file to `tmp/` AND print `[]` so nothing is announced; the orchestrator commits it like any other state.
- Produces: `commit_state(key: str)` as subcommand `fetch_sources.py commit-state <key>`: moves `tmp/<key>.state.json` to `state/<key>.json` (`feeds` -> `state/feeds_seen.json`, `releases` -> `state/releases.json`). No-op if the tmp file is absent.
- Uses a thread pool of 8 for the per-repo atom fetches (`concurrent.futures.ThreadPoolExecutor`), since 300 sequential fetches at 40 s timeout would blow the fetch budget.

- [ ] **Step 1: Write failing tests**: `test_releases_first_run_seeds_and_prints_empty`, `test_releases_new_entry_within_7_days`, `test_releases_old_entry_ignored_even_if_unseen`, `test_releases_starred_merge_and_failure_tolerated`, `test_commit_state_moves_file`, `test_commit_state_noop_when_absent`.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** tests, expect PASS. Live smoke: `python3 fetch_sources.py releases` prints `[]` and creates `tmp/releases.state.json` with > 6 repos. Do NOT commit-state yet (the orchestrator will on its first real run).
- [ ] **Step 5: Commit** `feat: releases fetcher via releases.atom with first-run seeding`.

### Task 6: claude_cli.py wrapper and probe

**Files:**
- Create: `claude_cli.py`, `tests/test_claude_cli.py`

**Interfaces:**
- Produces: `ask(model: str, prompt: str, timeout: int, schema: dict | None = None, runner=subprocess.run) -> str | dict | None`. Builds argv: `[CLAUDE_BIN, "-p", prompt, "--model", model, "--output-format", "json", "--no-session-persistence", "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']` plus `["--json-schema", json.dumps(schema)]` when a schema is given. `CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/home/inshal-rauf/.local/bin/claude")`. Runs with `capture_output=True, timeout=timeout, env={"HOME", "PATH"} from os.environ`. Returns the `structured_output` field when a schema was given, else the `result` string; `None` on non-zero exit, timeout, `is_error`, or unparseable stdout. Writes one stderr line on each failure with the first 200 chars of stderr.
- Produces: `parse_result(stdout: str) -> dict | None`: accepts either a single JSON object or a JSON array of events, finds the one with `"type": "result"`.
- Produces: `parse_json_text(text: str) -> dict | list | None`: strips ```` ``` ```` fences and leading/trailing prose, returns parsed JSON or None.
- Produces: `probe(timeout: int = 60) -> bool`: `ask("haiku", "Reply with exactly: OK", timeout)` returns a string containing `OK`.

- [ ] **Step 1: Write failing tests** with a fake `runner` capturing argv and returning canned `CompletedProcess`: `test_ask_argv_has_no_tools_and_no_mcp`, `test_ask_returns_result_string`, `test_ask_returns_structured_output_with_schema`, `test_ask_none_on_nonzero_exit`, `test_ask_none_on_timeout` (runner raises `subprocess.TimeoutExpired`), `test_parse_result_array_form`, `test_parse_result_strips_fences`, `test_probe_true_on_ok`.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** tests, expect PASS. Live check: `python3 -c "import claude_cli;print(claude_cli.probe())"` prints `True`. Then `python3 -c "import claude_cli,json;print(claude_cli.ask('haiku','Give one word for the colour of the sky as JSON',60,schema={'type':'object','properties':{'w':{'type':'string'}},'required':['w']}))"` prints a dict with key `w`. If `--json-schema` does not yield `structured_output` on this CLI version, fall back to `parse_json_text(result)` inside `ask` and note it in the commit body.
- [ ] **Step 5: Commit** `feat: claude -p wrapper with probe and structured output`.

### Task 7: curate.py, normalise and dedup

**Files:**
- Create: `curate.py`, `tests/test_curate_normalise.py`, `tests/fixtures/day/` (one small JSON per key in the exact shapes the fetchers print: `trending.json`, `huggingface.json`, `hackernews.json`, `newrepos.json`, `hnbest.json`, `feeds.json`, `reddit.json`)

**Interfaces:**
- Produces: `canonical_url(url: str) -> str`: lowercases scheme and host, strips `www.`, forces `https`, drops `utm_*`, `ref`, `fbclid` query params, drops fragment, strips a single trailing slash, keeps the path otherwise.
- Produces: `item_id(url: str) -> str`: first 12 hex of sha1 of `canonical_url(url)`.
- Produces: `normalise(day: dict[str, object]) -> list[dict]` where `day` maps key -> parsed JSON. Each output item: `{id, sources: [str], title, url, meta: str, hn_url: str | None}`. Merge rule: same `id` -> one item, `sources` appended in key order `trending, hackernews, hnbest, huggingface, newrepos, feeds, reddit`; first non-empty `title` wins; `hn_url` kept from any HN source; `meta` joined with ` · `. `trending` contributes each repo once even if it appears in several periods (meta notes the periods). `huggingface` items use the `id`/`title` as title. Reddit self posts use `permalink` as `url`.
- Produces: `load_day(sent_dir: str, date: str) -> dict[str, object]` reading `<sent_dir>/<date>-<key>.json` for every key that exists.

- [ ] **Step 1: Write failing tests**: `test_canonical_url_variants` (http/https, www, trailing slash, utm, fragment all map to one), `test_canonical_url_keeps_path` (`github.com/a/b` != `github.com/a/b/releases/tag/v1`), `test_normalise_merges_hn_and_feed_same_article` (one item, two sources, `hn_url` set), `test_normalise_trending_repo_once_across_periods`, `test_normalise_counts_match_fixture` (every fixture row accounted for either as its own item or as a merge; assert `len(items) == unique canonical urls across fixtures`), `test_load_day_missing_keys_skipped`.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** tests, expect PASS.
- [ ] **Step 5: Commit** `feat: curate normalise and canonical-URL dedup`.

### Task 8: curate.py, pass 1 gists, pass 2 themes, invariant, CLI

**Files:**
- Create: `prompts/gist.md`, `prompts/themes.md`
- Modify: `curate.py`
- Test: `tests/test_curate_model.py`

**Interfaces:**
- Consumes: `claude_cli.ask`, `claude_cli.probe` (injected as parameters so tests pass fakes).
- Produces: `GIST_SCHEMA` (object with `items: array of {id: string, gist: string, tags: array of string}`) and `THEMES_SCHEMA` (object with `themes: array of {title, summary, item_ids: array of string}`, `rest: array of string`).
- Produces: `gists(items: list[dict], ask, batch_size: int = 40, workers: int = 3, timeout: int = 120) -> list[dict]`: returns the same items with `gist` and `tags` set. Prompt = `prompts/gist.md` text + JSON of the batch (id, title, url, meta, sources). Missing id -> `gist = title`, `tags = []`. Gist longer than 140 chars truncated to 137 + `...`. A failed batch (ask returns None) retried once then falls back to titles. Uses `ThreadPoolExecutor(max_workers=workers)`.
- Produces: `themes(items: list[dict], ask, timeout: int = 300) -> dict`: one call with `prompts/themes.md` + JSON of `[{id, gist, tags, sources}]`. Returns `{themes: [...], rest: [...], grouped: bool}`; on failure (after one retry) returns `{themes: [], rest: [all ids], grouped: False}`.
- Produces: `enforce(items, result) -> dict`: removes unknown ids, keeps the first placement of a duplicate, appends forgotten ids to `rest`, dissolves themes left with fewer than 2 items into `rest`, orders themes by (distinct sources desc, item count desc). Guarantees `set(all placed ids) == set(item ids)`.
- Produces: `run(date: str, sent_dir: str, tmp_dir: str, ask=claude_cli.ask, probe=claude_cli.probe, no_model: bool = False) -> dict` writing `<sent_dir>/<date>-curated.json` = `{date, items: {id: item}, themes, rest, stats: {items_in, items_placed, sources: [keys present], grouped, probe_ok}}` and returning it. If `probe()` is False or `no_model`, skips both passes (gist = title, grouped False). Exits non-zero only if `items_in != items_placed` after `enforce` (which should be impossible; it is the belt to `enforce`'s braces).
- CLI: `python3 curate.py <date> <sent_dir> <tmp_dir> [--no-model]`.

- [ ] **Step 1: Write the prompts.** `prompts/gist.md`: "For each item write one plain-English sentence of at most 140 characters saying what it is, and 1 to 3 lowercase single-word tags. Do not judge importance. Return every id you were given." `prompts/themes.md`: "Group these items into themes. A theme needs at least two items that are about the same story, tool, or topic. Give each theme a short title and a summary of at most two sentences that names what the items have in common. Put every item that fits no theme in rest. Every id must appear exactly once across themes and rest. Do not rank by importance; the caller orders themes." No em dashes.
- [ ] **Step 2: Write failing tests** with fake `ask` callables: `test_gists_batches_and_fills_missing_with_title`, `test_gists_truncates_140`, `test_gists_failed_batch_falls_back_after_retry` (count ask calls == 2 for that batch), `test_themes_failure_returns_ungrouped`, `test_enforce_invariant_repairs_model_output` (duplicate id, unknown id, forgotten id, 1-item theme), `test_enforce_orders_by_sources_then_count`, `test_run_probe_false_skips_model_and_still_writes` (ask never called, file written, `grouped False`), `test_run_writes_curated_json_counts_match`.
- [ ] **Step 3: Run**, expect FAIL.
- [ ] **Step 4: Implement**.
- [ ] **Step 5: Run** tests, expect PASS. Live check on real data: copy today's `~/Learning Partner/sent/2026-10-29-{trending,huggingface,hackernews}.json` into `sent/`, run `python3 curate.py 2026-10-29 sent tmp`, confirm `sent/2026-10-29-curated.json` has `stats.items_in == stats.items_placed` and `grouped: true`, and print the first two theme titles in the commit body.
- [ ] **Step 6: Commit** `feat: curate passes, invariant enforcement, prompts`.

### Task 9: render_releases, build_eml attachment, em dash purge

**Files:**
- Modify: `render_email.py` (`build_eml`, `main`, strings containing U+2014)
- Test: `tests/test_render_releases.py`

**Interfaces:**
- Produces: `render_releases(data: list[dict], date: str) -> str` HTML using `_doc`, `_hdr("releases", "Releases", date, "new releases of tools you use")`, one row per item: repo as link, `tag` in a gray pill, `published` date, `notes`.
- Produces: `build_eml(subject, html_file, to_csv, out_path, attach: str | None = None)`. With `attach`, the message is `multipart/mixed` holding the existing `MIMEText(html)` part plus the file as `text/html` attachment named `os.path.basename(attach)`. Without `attach`, output is byte-identical to today (pinned by test comparing against the pre-change function captured in a fixture).
- `main` gains subcommands `releases <data_json> <date> <out_html>` and `eml` with an optional 5th positional `attach_path`.

- [ ] **Step 1: Capture a fixture** of current `build_eml` output for a small HTML file (`tests/fixtures/eml_before.eml`, with `FROM_ADDR` unset so the default From is used) BEFORE editing.
- [ ] **Step 2: Write failing tests**: `test_build_eml_without_attachment_unchanged` (compare to fixture, ignoring nothing), `test_build_eml_with_attachment_is_multipart` (parse with `email.message_from_bytes`, two parts, second has `Content-Disposition: attachment; filename="curated-2026-10-29.html"`), `test_render_releases_rows`, `test_no_em_dash_in_any_renderer` (render all five renderers on fixtures and heartbeat; assert `"—" not in html`).
- [ ] **Step 3: Run**, expect FAIL (em dash test fails on current strings).
- [ ] **Step 4: Implement**; replace the em dashes in `render_trending` and `render_hackernews` sub-lines with ` · `.
- [ ] **Step 5: Run** tests, expect PASS.
- [ ] **Step 6: Commit** `feat: releases renderer, eml attachments, remove em dashes`.

### Task 10: render_curated with body size rule

**Files:**
- Modify: `render_email.py`
- Test: `tests/test_render_curated.py`

**Interfaces:**
- Consumes: curated JSON shape from Task 8.
- Produces: `render_curated(cur: dict, date: str, body_limit: int = 95_000) -> tuple[str, str]` returning `(body_html, full_html)`. Full: counts line (`"{items_in} items from {n} sources, {t} themes"`), each theme as `group_header(title)` + summary paragraph + every item row (source pills, gist, link, `hn_url` as a second small link when present, meta), then `group_header("ALSO TODAY")` with `rest` items grouped by first source, then a footer line "The complete brief is attached as curated-<date>.html". Body: identical structure, but item rows are included theme by theme in order only while `len(body) <= body_limit`; a theme whose rows did not fit shows one line `"{n} items, see attachment"`; the ALSO TODAY section in the body is counts only when over the limit. When `grouped` is False: one section per source in fetch order, header "UNGROUPED" and subject suffix handled by the orchestrator.
- Row format reuses the `hackernews` row style; source pill colours: one `pill()` per source using `_gray_pill`.
- `main` gains `curated <curated_json> <date> <out_body_html> <out_full_html>`.

- [ ] **Step 1: Write failing tests**: `test_full_has_every_item_link` (every item url appears in full exactly once per item), `test_body_cut_at_theme_boundary` (build a curated dict with 30 themes of 20 long-gist items, `body_limit=20_000`; every theme in body either has all its rows or the "see attachment" line; `len(body) <= 20_000 + one theme's header size`), `test_attachment_has_every_item` (same dict, full contains all 600 urls), `test_ungrouped_renders_per_source`, `test_no_em_dash_curated`.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement**. Build the full first; build the body by re-walking themes and checking `len` after each theme block, never after each row.
- [ ] **Step 4: Run** tests, expect PASS. Render today's real curated JSON from Task 8 to `sent/2026-10-29-curated.html`; open it in a browser (`xdg-open`) and confirm it is readable top to bottom.
- [ ] **Step 5: Commit** `feat: curated renderer with attachment and body size rule`.

### Task 11: daily_brief.sh orchestration

**Files:**
- Modify: `daily_brief.sh`
- Test: `tests/test_orchestrator.sh` (bash, run with fake `GWS`, `PY` stubs via env) and `tests/test_orchestrator.py` wrapper that runs it under pytest.

**Interfaces:**
- `TOPIC_KEYS="trending huggingface hackernews newrepos hnbest feeds reddit releases"`; `RAW_EMAIL_KEYS="trending huggingface hackernews"`; `FETCH_ONLY_KEYS="newrepos hnbest feeds reddit"`; `LABELS` gains `[releases]="Releases"`.
- `GWS`, `PY`, `CLAUDE_BIN` are overridable from the environment (`GWS="${GWS:-/home/linuxbrew/.linuxbrew/bin/gws}"`) so tests can stub them.
- `process_snapshot` becomes three functions sharing a `fetch_topic` helper: `process_snapshot` (raw email keys, unchanged behaviour), `process_fetch_only` (fetch, archive JSON to `sent/`, `mark_done` on fetch success; exit code 3 from the fetcher -> `error`), `process_releases` (fetch, `[]` -> `empty` and `mark_done`, else render + send `Daily Brief · Releases · <date>`, then `fetch_sources.py commit-state releases` only after `sent`).
- Stage order: fetchers in parallel -> `wait` -> `curate` stage. Curate: skipped if `curate.done` exists and `ONLY` is empty, or if `ONLY` is set and does not include `curate`. Runs `timeout 1500 "$PY" curate.py "$TODAY" "$SENT" "$TMP"`, then `render_email.py curated`, then `eml` with the full HTML as attachment named `curated-$TODAY.html` (copy to `$TMP/curated-$TODAY.html` first so the attachment filename is right), subject `Daily Brief · Curated · $SUBJ_DATE` with suffix ` · ungrouped` when `stats.grouped` is false (read with a one-line `python3 -c`). On `sent`: `mark_done curate` and `fetch_sources.py commit-state feeds`. `--dry-run` renders but does not send and does not commit state.
- Health: `BAD` now also lists `curate(error)`, `probe(failed)` (from `stats.probe_ok` false) and any fetch-only topic in `error`.
- Heartbeat condition unchanged but computed over `RAW_EMAIL_KEYS` only.

- [ ] **Step 1: Write the bash test** `tests/test_orchestrator.sh`: creates a temp copy of the repo dir, stubs `GWS` (a script that logs its args and exits 0), `PY` (a script that dispatches: `fetch_sources.py <key>` copies `tests/fixtures/day/<key>.json` to stdout or prints `[]` for releases, `curate.py` writes a minimal curated JSON, `render_email.py` writes a file), runs `./daily_brief.sh`, then asserts: 3 raw sends + 1 curated send in the GWS log, `guard/<date>/curate.done` exists, fetch-only keys have `.done` and `sent/<date>-<key>.json`, a second run sends nothing, `--only=curate` sends exactly one more curated email. A second scenario makes the `feeds` stub exit 3 and asserts a health email was sent naming `feeds(error)` and the curated email still went out.
- [ ] **Step 2: Run** `python3 -m pytest tests/test_orchestrator.py -q`, expect FAIL.
- [ ] **Step 3: Implement** the script changes. Keep `set -uo pipefail`, keep the `log`/`should`/`run` helpers.
- [ ] **Step 4: Run** tests, expect PASS. `bash -n daily_brief.sh`. Real dry run: `./daily_brief.sh --dry-run` with the real `config.env`; confirm `cron.log` shows all 8 topics and the curate stage, and `sent/` has `<date>-curated.html` and `<date>-curated-body.html`.
- [ ] **Step 5: Commit** `feat: orchestrate new topics, releases email, curate stage`.

### Task 12: systemd switchover and verification under systemd

**Files:**
- Modify: `~/.config/systemd/user/daily-brief.service` (ExecStart path, Description, TimeoutStartSec=2400)
- Create: `docs/OPERATIONS.md` (how to run, `--only`, `--dry-run`, editing `feeds.opml` / `subreddits.txt` / `releases.txt`, where state lives, how to re-run curate)
- Modify: `README.md` (point to OPERATIONS.md)

- [ ] **Step 1: Edit the unit**: `ExecStart="%h/News Oxide/daily_brief.sh"`, `TimeoutStartSec=2400`, Description "News Oxide daily brief (raw snapshots + curated)". Leave the timer file untouched. `systemctl --user daemon-reload`.
- [ ] **Step 2: Verify `claude -p` under systemd**: `systemd-run --user --wait --pipe -p WorkingDirectory="%h/News Oxide" /usr/bin/python3 -c "import claude_cli;print(claude_cli.probe())"` must print `True`. If it prints `False`, inspect `journalctl --user -u run-*` for the stderr line and fix the environment in the unit (`Environment=HOME=%h` and `PATH`) before continuing.
- [ ] **Step 3: Full dry run under systemd**: temporarily run `systemd-run --user --wait --pipe "%h/News Oxide/daily_brief.sh" --dry-run --only=curate` and confirm `cron.log` shows `curate` finishing with `grouped=1`.
- [ ] **Step 4: First real send**, with the owner's go-ahead in chat: `./daily_brief.sh --only=releases,curate`. Expected in the owner's inbox: one Curated email with the attachment (no Releases email on first run, since state is seeded). Confirm the attachment opens on a phone.
- [ ] **Step 5: Write `docs/OPERATIONS.md`** and update `README.md`.
- [ ] **Step 6: Mark the design doc** status "implemented 2026-xx-xx" and commit `chore: switch timer to News Oxide, operations doc`.
- [ ] **Step 7: Retire the old location**: nothing is deleted. Add a one-line `~/Learning Partner/MOVED.md` pointing at `~/News Oxide` (outside this repo; just do it).

---

## Self-review notes

- Spec coverage: every source table row has a task (2, 3, 4, 5); normalise/dedup 7; pass 1 and 2, probe, pool of 3, invariant 8; curated output with attachment and body rule 10; releases email 9 and 11; stage order, guards, `--only=curate`, deadline, health 11; systemd verification and TimeoutStartSec 12; prompts in `prompts/` 8; `state/` tracked 3 and 5; no em dashes 9 and 10; recipients in `config.env` already done in the initial commit.
- Names used across tasks: `parse_feed`, `read_opml`, `parse_date` (1) used in 3 and 5; `get(url, headers=None)` (2) used in 3, 4, 5; `ask`, `probe` (6) used in 8; `canonical_url`, `item_id`, `normalise`, `load_day` (7) used in 8; `gists`, `themes`, `enforce`, `run` (8) and the curated JSON keys `items, themes, rest, stats.{items_in, items_placed, sources, grouped, probe_ok}` used in 10 and 11; `build_eml(..., attach=None)` (9) used in 11; `commit-state` (5) used in 11.

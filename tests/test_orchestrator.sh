#!/usr/bin/env bash
# Orchestrator test: runs daily_brief.sh in a temp dir against stub fetch/curate/render/gws.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(dirname "$HERE")"
FAILS=0
# Safety: never let this test reach the real gws. Abort unless the script takes GWS from the env.
grep -q 'GWS="${GWS:-' "$REPO/daily_brief.sh" || { echo "FAIL daily_brief.sh does not honour \$GWS; refusing to run"; exit 1; }
check(){ if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; FAILS=$((FAILS+1)); fi; }
count(){ grep -c -- "$1" "$GWS_LOG" 2>/dev/null || true; }

export TODAY_OVERRIDE="2026-01-15" SKIP_NETWORK_WAIT=1 FIXTURES="$HERE/fixtures/day"
D="$TODAY_OVERRIDE"; WS=""
trap 'rm -rf $WS' EXIT
setup(){
  W="$(mktemp -d)"; WS="$WS $W"; cp "$REPO/daily_brief.sh" "$W/"; cp "$HERE"/orchestrator_stubs/*.py "$W/"
  printf 'RECIPIENTS=a@example.org\nHEALTH_TO=h@example.org\nFROM_ADDR=f@example.org\n' > "$W/config.env"
  export GWS="$HERE/orchestrator_stubs/gws" GWS_LOG="$W/gws.log"; : > "$GWS_LOG"
  unset FAIL_KEYS GARBAGE_KEYS COMMIT_FAIL CURATE_EXIT CURATE_DEGRADED RELEASES_ITEMS RENDER_FAIL GWS_FAIL
}
G(){ echo "$W/guard/$D"; }

echo "## scenario 1: full run, rerun, --only=curate"
setup
bash "$W/daily_brief.sh"
check "3 raw sends" '[ "$(count "· GitHub Trending ·")" = 1 ] && [ "$(count "· Hugging Face ·")" = 1 ] && [ "$(count "· Hacker News ·")" = 1 ]'
check "1 curated send" '[ "$(count "· Curated ·")" = 1 ]'
check "0 releases sends" '[ "$(count "· Releases ·")" = 0 ]'
check "no health, no heartbeat" '[ "$(wc -l < "$GWS_LOG")" = 4 ]'
check "curated subject not ungrouped" '[ "$(count "ungrouped")" = 0 ]'
check "curated attachment named curated-<date>.html" 'grep "· Curated ·" "$GWS_LOG" | grep -q "tmp/curated-$D.html"'
check "curate.done" '[ -f "$(G)/curate.done" ]'
check "releases.done (empty)" '[ -f "$(G)/releases.done" ]'
for k in newrepos hnbest feeds reddit; do
  check "$k.done + archived" '[ -f "$(G)/$k.done" ] && [ -f "$W/sent/$D-$k.json" ]'
done
check "curated html files" '[ -f "$W/sent/$D-curated.html" ] && [ -f "$W/sent/$D-curated-body.html" ]'
check "commit-state feeds after curated send" '[ -f "$W/tmp/committed-feeds" ]'
check "curate called with model" 'grep -q "\"no_model\": false" "$W/sent/$D-curated.json"'
cp "$W/cron.log" "$W/cron.log.1"
bash "$W/daily_brief.sh"
check "rerun sends nothing" '[ "$(wc -l < "$GWS_LOG")" = 4 ]'
check "rerun logs already done" 'grep -q "curate: already done" "$W/cron.log" && grep -q "trending: already done" "$W/cron.log"'
bash "$W/daily_brief.sh" --only=curate
check "--only=curate sends one more curated" '[ "$(count "· Curated ·")" = 2 ] && [ "$(wc -l < "$GWS_LOG")" = 5 ]'
bash "$W/daily_brief.sh" --only=trending
check "--only=trending does not run curate" '[ "$(count "· Curated ·")" = 2 ] && [ "$(count "· GitHub Trending ·")" = 2 ]'

echo "## scenario 2: feeds fails (exit 3), hnbest prints non-JSON"
setup; export FAIL_KEYS=feeds GARBAGE_KEYS=hnbest
bash "$W/daily_brief.sh"
check "health names feeds(error)" 'grep "· ⚠ health ·" "$GWS_LOG" | grep -q . && grep -q "feeds(error)" "$W/tmp/health.html"'
check "health goes to HEALTH_TO" 'grep "· ⚠ health ·" "$GWS_LOG" | grep -q "h@example.org"'
check "curated still sent" '[ "$(count "· Curated ·")" = 1 ]'
check "feeds not done" '[ ! -f "$(G)/feeds.done" ]'
check "non-JSON fetch is hnbest(error), not done" 'grep -q "hnbest(error)" "$W/tmp/health.html" && [ ! -f "$(G)/hnbest.done" ]'

echo "## scenario 3: curate exit 2 still sends, flags curate(error)"
setup; export CURATE_EXIT=2
bash "$W/daily_brief.sh"
check "curated sent despite exit 2" '[ "$(count "· Curated ·")" = 1 ]'
check "health names curate(error)" 'grep -q "curate(error)" "$W/tmp/health.html"'

echo "## scenario 4: releases with items"
setup; export RELEASES_ITEMS=1
bash "$W/daily_brief.sh"
check "releases email sent" '[ "$(count "· Releases ·")" = 1 ]'
check "commit-state releases" '[ -f "$W/tmp/committed-releases" ]'

echo "## scenario 5: probe failed, ungrouped"
setup; export CURATE_DEGRADED=1
bash "$W/daily_brief.sh"
check "curated subject says ungrouped" 'grep "· Curated ·" "$GWS_LOG" | grep -q "· ungrouped"'
check "health names probe(failed)" 'grep -q "probe(failed)" "$W/tmp/health.html"'
check "curate itself not flagged" '! grep -q "curate(error)" "$W/tmp/health.html"'

echo "## scenario 6: curated render fails"
setup; export RENDER_FAIL=curated
bash "$W/daily_brief.sh"
check "no curated send" '[ "$(count "· Curated ·")" = 0 ] && [ "$(count "· GitHub Trending ·")" = 1 ]'
check "no curate.done, no feeds commit" '[ ! -f "$(G)/curate.done" ] && [ ! -f "$W/tmp/committed-feeds" ]'
check "health names curate(error)" 'grep "· ⚠ health ·" "$GWS_LOG" | grep -q . && grep -q "curate(error)" "$W/tmp/health.html"'

echo "## scenario 7: eml build fails, stale emls from an earlier day are present"
setup; export RENDER_FAIL=eml; mkdir -p "$W/tmp"
for k in trending huggingface hackernews curate health; do echo "eml STALE $k" > "$W/tmp/$k.eml"; done
bash "$W/daily_brief.sh"
check "nothing sent (no stale eml uploaded)" '[ ! -s "$GWS_LOG" ]'
check "no .done for raw or curate" '[ ! -f "$(G)/trending.done" ] && [ ! -f "$(G)/curate.done" ] && [ ! -f "$W/tmp/committed-feeds" ]'
check "health lists trending(error) and curate(error)" 'grep -q "trending(error)" "$W/tmp/health.html" && grep -q "curate(error)" "$W/tmp/health.html"'

echo "## scenario 8: curated send fails"
setup; export GWS_FAIL="· Curated ·"
bash "$W/daily_brief.sh"
check "raw sent, curated not" '[ "$(count "· GitHub Trending ·")" = 1 ] && [ "$(count "· Curated ·")" = 0 ]'
check "no curate.done, no feeds commit" '[ ! -f "$(G)/curate.done" ] && [ ! -f "$W/tmp/committed-feeds" ]'
check "health sent naming curate(error)" 'grep -q "· ⚠ health ·" "$GWS_LOG" && grep -q "curate(error)" "$W/tmp/health.html"'
setup; export GWS_FAIL=1
bash "$W/daily_brief.sh"
check "GWS_FAIL=1: nothing done" '[ -z "$(ls "$(G)" | grep -v -e newrepos -e hnbest -e feeds -e reddit -e releases)" ] && [ ! -f "$W/tmp/committed-feeds" ]'
check "GWS_FAIL=1: health.html names curate(error)" 'grep -q "curate(error)" "$W/tmp/health.html"'

echo "## scenario 9: commit-state fails"
setup; export COMMIT_FAIL=1
bash "$W/daily_brief.sh"
check "health names feeds/releases(commit-failed)" 'grep -q "feeds(commit-failed)" "$W/tmp/health.html" && grep -q "releases(commit-failed)" "$W/tmp/health.html"'
check "commit failure logged" 'grep -q "feeds: commit-state FAILED" "$W/cron.log"'

echo "## scenario 10: every source fails"
setup; export FAIL_KEYS=trending,huggingface,hackernews,newrepos,hnbest,feeds,reddit
bash "$W/daily_brief.sh"
check "no curated email" '[ "$(count "· Curated ·")" = 0 ] && [ ! -f "$(G)/curate.done" ]'
check "logs nothing to curate" 'grep -q "curate: nothing to curate" "$W/cron.log"'
check "health lists the topics, not curate" 'grep -q "reddit(error)" "$W/tmp/health.html" && ! grep -q "curate(" "$W/tmp/health.html"'
check "only the health email went out" '[ "$(wc -l < "$GWS_LOG")" = 1 ]'

echo "## scenario 11: --dry-run"
setup; export RELEASES_ITEMS=1
bash "$W/daily_brief.sh" --dry-run
check "dry-run sends nothing" '[ ! -s "$GWS_LOG" ]'
check "dry-run writes no .done" '[ -z "$(ls "$(G)" 2>/dev/null)" ]'
check "dry-run commits no state" '[ -z "$(ls "$W"/tmp/committed-* 2>/dev/null)" ]'
check "dry-run curate --no-model" 'grep -q "\"no_model\": true" "$W/sent/$D-curated.json"'
check "dry-run renders curated" '[ -f "$W/sent/$D-curated.html" ] && [ -f "$W/sent/$D-curated-body.html" ]'

echo "## result: $FAILS failure(s)"
[ "$FAILS" -eq 0 ]

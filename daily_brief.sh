#!/usr/bin/env bash
#
# daily_brief.sh: Daily brief. Eight fetch topics, three raw snapshot emails,
# a releases email, and one curated email.
#   trending huggingface hackernews  fetch -> render -> send raw (no LLM)
#   releases                         fetch -> render -> send when there are new releases
#   newrepos hnbest feeds reddit     fetch -> archive JSON to sent/ (curate input only)
#   curate                           after all fetches: curate.py -> render -> send
# Fetch topics run IN PARALLEL; curate runs after them, so raw emails never wait on the model.
# Run by a systemd user timer at 12:00 PKT (Persistent=true, catches up after boot).
# Usage:  daily_brief.sh [--dry-run] [--only=trending,curate]

set -uo pipefail
shopt -u patsub_replacement 2>/dev/null || true   # literal '&' in ${var//pat/repl}

export HOME="/home/inshal-rauf"
export PATH="$HOME/.local/bin:/home/linuxbrew/.linuxbrew/bin:/usr/local/bin:/usr/bin:/bin"

GWS="${GWS:-/home/linuxbrew/.linuxbrew/bin/gws}"
PY="${PY:-python3}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; SENT="$DIR/sent"; TMP="$DIR/tmp"
mkdir -p "$SENT" "$TMP"; cd "$DIR" || exit 1   # gws only uploads files under its cwd
LOG="$DIR/cron.log"

FETCH_TIMEOUT=200      # most fetches
SLOW_TIMEOUT=400       # releases (~240 feeds), reddit (sleeps between subreddits)
CURATE_TIMEOUT=1500    # curate stage, so a slow model cannot block the health alert
# Recipients live in config.env (untracked). See config.env.example.
CONFIG="$DIR/config.env"; [ -f "$CONFIG" ] && . "$CONFIG"
RECIPIENTS="${RECIPIENTS:?set RECIPIENTS in config.env}"; export RECIPIENTS HEALTH_TO FROM_ADDR
TOPIC_KEYS="trending huggingface hackernews newrepos hnbest feeds reddit releases"
RAW_EMAIL_KEYS="trending huggingface hackernews"
FETCH_ONLY_KEYS="newrepos hnbest feeds reddit"

declare -A LABELS=( [trending]="GitHub Trending" [huggingface]="Hugging Face" [hackernews]="Hacker News"
                    [newrepos]="New Repos" [hnbest]="HN Best" [feeds]="Feeds" [reddit]="Reddit" [releases]="Releases" )

DRYRUN=0; ONLY=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRYRUN=1 ;;
    --only=*)  ONLY="${a#--only=}" ;;   # e.g. --only=trending : run just these, bypass guard
  esac
done
TODAY="${TODAY_OVERRIDE:-$(date +%Y-%m-%d)}"; SUBJ_DATE="$(date -d "$TODAY" +'%b %d')"
GUARDDIR="$DIR/guard/$TODAY"; mkdir -p "$GUARDDIR"
topic_done(){ [ -f "$GUARDDIR/$1.done" ]; }
mark_done(){ [ "$DRYRUN" -eq 1 ] || : > "$GUARDDIR/$1.done"; }
status(){ echo "$2" > "$TMP/$1.status"; }
# run <key>: selected and not done today. A guard skip writes status "done" so it is not "missing".
run(){ should "$1" || return 1; if [ -z "$ONLY" ] && topic_done "$1"; then log "$1: already done today, skipping"; status "$1" done; return 1; fi; }
# commit_state <key>: promote pending state; a failure is logged and flagged for the health alert.
commit_state(){ "$PY" "$DIR/fetch_sources.py" commit-state "$1" 2>>"$LOG" || { log "$1: commit-state FAILED"; : > "$TMP/$1.commitfail"; }; }
# build_eml <subject> <html> <to> <eml> [attach]: removes any old eml first, so a failed build never sends a stale one.
build_eml(){ rm -f "$4"; "$PY" "$DIR/render_email.py" eml "$@" && [ -s "$4" ]; }
send(){ "$GWS" gmail users messages send --params '{"userId":"me"}' --upload "$1" --upload-content-type message/rfc822 >>"$LOG" 2>&1; }

log(){ echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*" >> "$LOG"; }
should(){ [ -z "$ONLY" ] || case ",$ONLY," in *",$1,"*) return 0;; *) return 1;; esac; }

# Wait until the network reaches the internet (login-time catch-ups can race DNS). Poll up to ~3 min.
wait_for_network(){
  [ "${SKIP_NETWORK_WAIT:-0}" = 1 ] && return 0
  local i=0
  until curl -sf --max-time 5 -o /dev/null https://www.google.com 2>/dev/null \
        || curl -sf --max-time 5 -o /dev/null https://api.github.com 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -ge 36 ]; then log "network: still unreachable after ~180s; proceeding anyway"; return 1; fi
    [ "$i" -eq 1 ] && log "network: not ready at start, waiting for connectivity..."
    sleep 5
  done
  [ "$i" -gt 0 ] && log "network: came up after ~$((i*5))s"
  return 0
}

# fetch_topic <key>: fetch into $TMP/<key>_data.json, set TOTAL. Non-zero (status error) on failure.
fetch_topic(){
  local key="$1" t="$FETCH_TIMEOUT"; case "$key" in releases|reddit) t="$SLOW_TIMEOUT";; esac
  log "--- topic: $key (${LABELS[$key]}) START ---"
  SJ="$TMP/${key}_data.json"; rm -f "$TMP/$key.state.json"   # pending state must be from this fetch
  timeout "$t" "$PY" "$DIR/fetch_sources.py" "$key" > "$SJ" 2>>"$LOG" \
    || { log "$key: fetch failed (exit $?)"; status "$key" error; return 1; }
  TOTAL=$("$PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(sum(len(v) for v in d.values()) if isinstance(d,dict) else len(d))" "$SJ" 2>>"$LOG") \
    || { log "$key: fetch output is not JSON"; status "$key" error; return 1; }
  log "$key: fetched $TOTAL items"
}

# render_send <key> <html>: eml + send. Status sent (and mark_done) or error.
render_send(){
  local key="$1" html="$2" eml="$TMP/$1.eml"
  build_eml "Daily Brief · ${LABELS[$key]} · $SUBJ_DATE" "$html" "$RECIPIENTS" "$eml" \
    || { status "$key" error; log "$key: ERROR building eml"; return 1; }
  if send "$eml"; then status "$key" sent; mark_done "$key"; log "$key: SENT $TOTAL items to $RECIPIENTS"
  else status "$key" error; log "$key: ERROR sending"; return 1; fi
}

# Raw snapshot topic (own background subshell): fetch -> render -> send.
process_snapshot(){
  local key="$1"
  fetch_topic "$key" || return
  if [ "$TOTAL" -eq 0 ]; then status "$key" empty; return; fi
  local shtml="$SENT/$TODAY-$key.html"; cp "$SJ" "$SENT/$TODAY-$key.json"
  "$PY" "$DIR/render_email.py" "$key" "$SJ" "$TODAY" "$shtml" \
    || { log "$key: render failed"; status "$key" error; return 1; }
  if [ "$DRYRUN" -eq 1 ]; then log "$key: DRY-RUN rendered $shtml ($TOTAL items)"; status "$key" produced; return; fi
  render_send "$key" "$shtml"
}

# Fetch-only topic: archive JSON to sent/ for curate; done on a successful fetch.
process_fetch_only(){
  local key="$1"
  fetch_topic "$key" || return
  if [ "$TOTAL" -eq 0 ]; then status "$key" empty; mark_done "$key"; return; fi
  cp "$SJ" "$SENT/$TODAY-$key.json"; status "$key" produced; mark_done "$key"
  log "$key: archived $SENT/$TODAY-$key.json"
}

# Releases: email only when there are new releases; state committed only after send (or when empty).
process_releases(){
  local key=releases
  fetch_topic "$key" || return
  if [ "$TOTAL" -eq 0 ]; then
    status "$key" empty; mark_done "$key"
    # Empty is final (first run seeds state silently); nothing to re-announce, so commit.
    [ "$DRYRUN" -eq 0 ] && commit_state releases
    return
  fi
  local shtml="$SENT/$TODAY-$key.html"; cp "$SJ" "$SENT/$TODAY-$key.json"
  "$PY" "$DIR/render_email.py" "$key" "$SJ" "$TODAY" "$shtml" \
    || { log "$key: render failed"; status "$key" error; return 1; }
  if [ "$DRYRUN" -eq 1 ]; then log "$key: DRY-RUN rendered $shtml ($TOTAL items)"; status "$key" produced; return; fi
  render_send "$key" "$shtml" && commit_state releases
}

# Curate stage (foreground, after all fetches). Writes $TMP/curate.status and $TMP/probe.status.
process_curate(){
  log "--- stage: curate START ---"
  local cj="$SENT/$TODAY-curated.json" body="$SENT/$TODAY-curated-body.html" full="$SENT/$TODAY-curated.html"
  local nm=(); [ "$DRYRUN" -eq 1 ] && nm=(--no-model)
  rm -f "$cj"
  timeout "$CURATE_TIMEOUT" "$PY" "$DIR/curate.py" "$TODAY" "$SENT" "$TMP" "${nm[@]}" >>"$LOG" 2>&1
  local rc=$?
  if [ "$rc" -eq 2 ] && [ -f "$cj" ]; then log "curate: invariant failure (exit 2), sending anyway"; status curate error
  elif [ "$rc" -ne 0 ] || [ ! -f "$cj" ]; then log "curate: FAILED (exit $rc), no curated email"; status curate error; return; fi
  local flags; flags=$("$PY" -c "import json,sys;s=json.load(open(sys.argv[1]))['stats'];print(s.get('items_in', 0), int(bool(s.get('grouped'))), {None:'skipped',True:'ok',False:'failed'}[s.get('probe_ok')])" "$cj" 2>>"$LOG") \
    || { log "curate: unreadable stats in $cj"; status curate error; return; }
  local n grouped probe; read -r n grouped probe <<< "$flags"; status probe "$probe"
  if [ "$n" -eq 0 ]; then log "curate: nothing to curate (no source produced items), no curated email"; status curate empty; return; fi
  "$PY" "$DIR/render_email.py" curated "$cj" "$TODAY" "$body" "$full" \
    || { log "curate: render failed"; status curate error; return; }
  if [ "$DRYRUN" -eq 1 ]; then log "curate: DRY-RUN rendered $full (grouped=$grouped)"; [ -f "$TMP/curate.status" ] || status curate produced; return; fi
  local att="$TMP/curated-$TODAY.html" subj="Daily Brief · Curated · $SUBJ_DATE"
  [ "$grouped" = 1 ] || subj="$subj · ungrouped"
  rm -f "$att"
  { cp "$full" "$att" && build_eml "$subj" "$body" "$RECIPIENTS" "$TMP/curate.eml" "$att"; } \
    || { log "curate: ERROR building eml"; status curate error; return; }
  if send "$TMP/curate.eml"; then
    [ -f "$TMP/curate.status" ] || status curate sent
    mark_done curate; commit_state feeds; log "curate: SENT to $RECIPIENTS"
  else status curate error; log "curate: ERROR sending"; fi
}

# --- Main -------------------------------------------------------------------
log "########## Daily brief: $TODAY (dryrun=$DRYRUN) ##########"
[ -n "$ONLY" ] && log "Partial run, only: $ONLY (guard bypassed)"
rm -f "$TMP"/*.status "$TMP"/*.commitfail 2>/dev/null

wait_for_network

for key in $TOPIC_KEYS; do
  case " $FETCH_ONLY_KEYS " in *" $key "*) fn=process_fetch_only;; *) fn=process_snapshot;; esac
  [ "$key" = releases ] && fn=process_releases
  run "$key" && "$fn" "$key" &
done
wait

run curate && process_curate

# Tally (raw email topics only: the heartbeat stands in for the three raw emails)
PRODUCED=0; ERRORED=0
for key in $RAW_EMAIL_KEYS; do
  st="$(cat "$TMP/${key}.status" 2>/dev/null || echo missing)"
  case "$st" in sent|produced|done) PRODUCED=1;; error|missing) ERRORED=1;; esac
done

# Heartbeat: only when everything was genuinely empty (no errors), never mask failures.
if [ "$PRODUCED" -eq 0 ] && [ "$ERRORED" -eq 0 ] && [ -z "$ONLY" ]; then
  log "All topics genuinely empty: heartbeat."
  hb="$SENT/$TODAY-heartbeat.html"; "$PY" "$DIR/render_email.py" heartbeat "$TODAY" "$hb"
  if [ "$DRYRUN" -eq 0 ]; then
    build_eml "Daily Brief · nothing today · $SUBJ_DATE" "$hb" "$RECIPIENTS" "$TMP/heartbeat.eml" \
      && send "$TMP/heartbeat.eml" && log "heartbeat: SENT" || log "heartbeat: ERROR sending"
  else log "heartbeat: DRY-RUN, not sending."; fi
fi

# Health alert: silent failure is the #1 fear. If anything errored or is missing, email the user (only).
BAD=""
for key in $TOPIC_KEYS; do
  st="$(cat "$TMP/$key.status" 2>/dev/null || echo missing)"
  case "$st" in error|missing) BAD="$BAD $key($st)";; esac
done
[ "$(cat "$TMP/curate.status" 2>/dev/null)" = error ] && BAD="$BAD curate(error)"
[ "$(cat "$TMP/probe.status" 2>/dev/null)" = failed ] && BAD="$BAD probe(failed)"
for f in "$TMP"/*.commitfail; do [ -e "$f" ] && k="${f##*/}" && BAD="$BAD ${k%.commitfail}(commit-failed)"; done
[ -n "$BAD" ] && log "health: needs attention:$BAD"
if [ "$DRYRUN" -eq 0 ] && [ -z "$ONLY" ] && [ -n "$BAD" ]; then
  printf '<pre style="font-family:monospace;font-size:14px;color:#c9d1d9;background:#0d1117;padding:16px">Daily Brief health: %s\n\nNeeds attention:%s\n\nSee %s/cron.log for detail.</pre>' "$TODAY" "$BAD" "$DIR" > "$TMP/health.html"
  build_eml "Daily Brief · ⚠ health · $SUBJ_DATE" "$TMP/health.html" "${HEALTH_TO:-$RECIPIENTS}" "$TMP/health.eml" \
    && send "$TMP/health.eml" && log "health: alerted:$BAD" || log "health: alert send failed"
fi
log "########## Run complete (produced=$PRODUCED, dryrun=$DRYRUN) ##########"

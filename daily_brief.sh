#!/usr/bin/env bash
#
# daily_brief.sh — Daily snapshot brief: 3 deterministic emails (no LLM).
#   trending     GitHub trending repos (Trendshift mirror)
#   huggingface  trending models/datasets/spaces + daily papers (HF API)
#   hackernews   top front-page stories (HN Algolia)
# Per topic: fetch -> render -> send. Topics run IN PARALLEL.
# Run by a systemd user timer at 12:00 PKT (Persistent=true → catches up after boot).
# Usage:  daily_brief.sh [--dry-run] [--only=trending,hackernews]

set -uo pipefail
shopt -u patsub_replacement 2>/dev/null || true   # literal '&' in ${var//pat/repl}

export HOME="/home/inshal-rauf"
export PATH="$HOME/.local/bin:/home/linuxbrew/.linuxbrew/bin:/usr/local/bin:/usr/bin:/bin"

GWS="/home/linuxbrew/.linuxbrew/bin/gws"
PY="python3"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; SENT="$DIR/sent"; TMP="$DIR/tmp"
mkdir -p "$SENT" "$TMP"
LOG="$DIR/cron.log"

FETCH_TIMEOUT=200      # snapshot fetches
# Recipients live in config.env (untracked). See config.env.example.
CONFIG="$DIR/config.env"; [ -f "$CONFIG" ] && . "$CONFIG"
RECIPIENTS="${RECIPIENTS:?set RECIPIENTS in config.env}"; export RECIPIENTS HEALTH_TO FROM_ADDR
TOPIC_KEYS="trending huggingface hackernews"

declare -A LABELS=( [trending]="GitHub Trending" [huggingface]="Hugging Face" [hackernews]="Hacker News" )

DRYRUN=0; ONLY=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRYRUN=1 ;;
    --only=*)  ONLY="${a#--only=}" ;;   # e.g. --only=trending : run just these, bypass guard
  esac
done
TODAY="$(date +%Y-%m-%d)"; SUBJ_DATE="$(date +'%b %d')"
GUARDDIR="$DIR/guard/$TODAY"; mkdir -p "$GUARDDIR"
topic_done(){ [ -f "$GUARDDIR/$1.done" ]; }
mark_done(){ : > "$GUARDDIR/$1.done"; }
run(){ should "$1" || return 1; if [ -z "$ONLY" ] && topic_done "$1"; then log "$1: already done today, skipping"; return 1; fi; }

log(){ echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*" >> "$LOG"; }
should(){ [ -z "$ONLY" ] || case ",$ONLY," in *",$1,"*) return 0;; *) return 1;; esac; }

# Wait until the network reaches the internet (login-time catch-ups can race DNS). Poll up to ~3 min.
wait_for_network(){
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

# Process one snapshot topic (own background subshell). Writes $TMP/<key>.status.
process_snapshot(){
  local key="$1" label="$2"
  log "--- topic: $key ($label) START ---"
  local sj="$TMP/${key}_data.json"
  timeout "$FETCH_TIMEOUT" "$PY" "$DIR/fetch_sources.py" "$key" > "$sj" 2>>"$LOG" \
    || { log "$key: fetch failed"; echo error > "$TMP/${key}.status"; return; }
  local total; total=$("$PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(sum(len(v) for v in d.values()) if isinstance(d,dict) else len(d))" "$sj" 2>/dev/null || echo 0)
  log "$key: fetched $total items"
  if [ "$total" -eq 0 ]; then echo empty > "$TMP/${key}.status"; return; fi

  local shtml="$SENT/$TODAY-$key.html"; cp "$sj" "$SENT/$TODAY-$key.json"
  "$PY" "$DIR/render_email.py" "$key" "$sj" "$TODAY" "$shtml"
  if [ "$DRYRUN" -eq 1 ]; then log "$key: DRY-RUN rendered $shtml ($total items)"; echo produced > "$TMP/${key}.status"; return; fi

  local ssub="Daily Brief · $label · $SUBJ_DATE" seml="$TMP/${key}.eml"
  "$PY" "$DIR/render_email.py" eml "$ssub" "$shtml" "$RECIPIENTS" "$seml"
  if "$GWS" gmail users messages send --params '{"userId":"me"}' --upload "$seml" --upload-content-type message/rfc822 >>"$LOG" 2>&1; then
    echo sent > "$TMP/${key}.status"; mark_done "$key"; log "$key: SENT $total items to $RECIPIENTS"
  else echo error > "$TMP/${key}.status"; log "$key: ERROR sending"; fi
}

# --- Main -------------------------------------------------------------------
log "########## Daily snapshot brief — $TODAY (dryrun=$DRYRUN) ##########"
[ -n "$ONLY" ] && log "Partial run — only: $ONLY (guard bypassed)"
rm -f "$TMP"/*.status 2>/dev/null

wait_for_network

for key in $TOPIC_KEYS; do
  run "$key" && process_snapshot "$key" "${LABELS[$key]}" &
done
wait

# Tally
PRODUCED=0; ERRORED=0
for key in $TOPIC_KEYS; do
  st="$(cat "$TMP/${key}.status" 2>/dev/null || echo missing)"
  { [ "$st" = "sent" ] || [ "$st" = "produced" ]; } && PRODUCED=1
  { [ "$st" = "error" ] || [ "$st" = "missing" ]; } && ERRORED=1
done

# Heartbeat: only when everything was genuinely empty (no errors) — never mask failures.
if [ "$PRODUCED" -eq 0 ] && [ "$ERRORED" -eq 0 ] && [ -z "$ONLY" ]; then
  log "All topics genuinely empty — heartbeat."
  hb="$SENT/$TODAY-heartbeat.html"; "$PY" "$DIR/render_email.py" heartbeat "$TODAY" "$hb"
  if [ "$DRYRUN" -eq 0 ]; then
    "$PY" "$DIR/render_email.py" eml "Daily Brief · nothing today · $SUBJ_DATE" "$hb" "$RECIPIENTS" "$TMP/heartbeat.eml"
    "$GWS" gmail users messages send --params '{"userId":"me"}' --upload "$TMP/heartbeat.eml" --upload-content-type message/rfc822 >>"$LOG" 2>&1 \
      && log "heartbeat: SENT" || log "heartbeat: ERROR sending"
  else log "heartbeat: DRY-RUN, not sending."; fi
fi

# Health alert: silent failure is the #1 fear — if any topic errored/missing, email the user (only).
if [ "$DRYRUN" -eq 0 ] && [ -z "$ONLY" ]; then
  BAD=""
  for key in $TOPIC_KEYS; do
    st="$(cat "$TMP/$key.status" 2>/dev/null || echo missing)"
    case "$st" in error|missing) BAD="$BAD $key($st)";; esac
  done
  if [ -n "$BAD" ]; then
    printf '<pre style="font-family:monospace;font-size:14px;color:#c9d1d9;background:#0d1117;padding:16px">Daily Brief health — %s\n\nNeeds attention:%s\n\nSee ~/Learning Partner/cron.log for detail.</pre>' "$TODAY" "$BAD" > "$TMP/health.html"
    "$PY" "$DIR/render_email.py" eml "Daily Brief · ⚠ health · $SUBJ_DATE" "$TMP/health.html" "${HEALTH_TO:-$RECIPIENTS}" "$TMP/health.eml"
    "$GWS" gmail users messages send --params '{"userId":"me"}' --upload "$TMP/health.eml" --upload-content-type message/rfc822 >>"$LOG" 2>&1 \
      && log "health: alerted →$BAD" || log "health: alert send failed"
  fi
fi
log "########## Run complete (produced=$PRODUCED, dryrun=$DRYRUN) ##########"

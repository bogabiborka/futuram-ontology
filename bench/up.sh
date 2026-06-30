#!/usr/bin/env bash
# Bring the bench stack up (detached) AND keep the Mac awake for as long as the
# stack runs, so a benchmark keeps running with the monitor asleep / lid closed.
#
# WHY a host script: the bench runs INSIDE the futuram-bench-observer container
# (in Docker Desktop's Linux VM). macOS idle-sleep happens on the HOST, outside
# that VM, and `caffeinate` is a host-only binary — nothing `docker compose up`
# starts can reach it. So sleep-prevention must be launched here, on the host.
#
# `caffeinate -i -s` holds an idle-sleep assertion (system stays awake; the
# DISPLAY may still sleep — exactly "run in the background on monitor sleep").
# We run it detached and pin its PID so `down.sh` releases it; if this script is
# re-run it replaces a stale assertion rather than stacking them.
#
# Usage:   bench/up.sh [extra docker compose up args]
#          RELOAD=1 bench/up.sh        # rebuild the served TDB from staging
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE=(docker compose -f "$REPO/docker-compose.yml")
PIDFILE="$REPO/bench/.caffeinate.pid"

start_caffeinate() {
  if [[ "$(uname)" != "Darwin" ]]; then
    echo "[up] not macOS — skipping caffeinate (no host idle-sleep to prevent)"
    return
  fi
  # release a stale assertion from a previous up.sh first
  if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "[up] caffeinate already holding the Mac awake (pid $(cat "$PIDFILE"))"
    return
  fi
  # -i: prevent idle SYSTEM sleep   -s: assert only on AC power (matches a desk run)
  # The display is intentionally NOT kept on (no -d), so the monitor can sleep.
  caffeinate -i -s &
  echo $! > "$PIDFILE"
  echo "[up] caffeinate holding the Mac awake (pid $!) — display may still sleep"
}

start_caffeinate
echo "[up] bringing the bench stack up (detached)…"
"${COMPOSE[@]}" up -d "$@"
echo "[up] stack up. The run survives monitor sleep. Stop with: bench/down.sh"

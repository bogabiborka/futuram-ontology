#!/usr/bin/env bash
# Stop the bench stack AND release the host caffeinate assertion that up.sh held,
# so the Mac can idle-sleep normally again once no run is active.
#
# Usage:   bench/down.sh [extra docker compose down args]
#          bench/down.sh -v          # also drop volumes
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE=(docker compose -f "$REPO/docker-compose.yml")
PIDFILE="$REPO/bench/.caffeinate.pid"

if [[ -f "$PIDFILE" ]]; then
  PID="$(cat "$PIDFILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null && echo "[down] released caffeinate (pid $PID) — Mac may idle-sleep again"
  fi
  rm -f "$PIDFILE"
fi

echo "[down] stopping the bench stack…"
"${COMPOSE[@]}" down "$@"

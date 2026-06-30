#!/usr/bin/env bash
# bench/run.sh — the ONE blessed way to operate the fq-vs-composition bench.
#
# Solves the recurring "localhost:3737 not updating" trap: the run and the
# observer MUST share one live-transcript dir. This script pins that dir
# (BENCH_LIVE_DIR) and the testcases file (BENCH_TESTCASES) ONCE, then wires every
# component to them:
#   * the observer reads $BENCH_LIVE_DIR        (bench/observer/lib/runs.js)
#   * run_bench writes $BENCH_LIVE_DIR          (resolve_live_dir in run_bench.py)
#   * both default to bench/live if unset, so they agree even without this script.
#
# So a run ALWAYS streams to the page. No flag to remember, no dir to guess.
#
# Steps (each independently skippable):
#   1. (re)load the bench Fuseki + MCP    — picks up rebuilt data + NEW skill files
#   2. regenerate bench/testcases/domain.yaml from the live endpoint
#   3. start/refresh the observer on :3737, pointed at $BENCH_LIVE_DIR
#   4. run the bench (BOTH backends by default) into $BENCH_LIVE_DIR
#
# Usage:
#   bash bench/run.sh                          # full: reload, regen, observe, run
#   bash bench/run.sh --backends fq            # one backend
#   bash bench/run.sh --model gemma4:31b-cloud # explicit model (NEVER deepseek)
#   bash bench/run.sh --skip-reload --skip-regen   # just run the bench + observe
#   bash bench/run.sh --skip-observer          # headless (no website)
set -euo pipefail
cd "$(dirname "$0")/.."

# --- the two knobs that everything else hangs off (export so children inherit) ---
export BENCH_LIVE_DIR="${BENCH_LIVE_DIR:-$(pwd)/bench/live}"
export BENCH_TESTCASES="${BENCH_TESTCASES:-$(pwd)/bench/testcases/domain.yaml}"

MODEL="${BENCH_MODEL:-gemma4:31b-cloud}"   # NEVER deepseek (run_bench guards anyway)
# fq only for now (composition is disabled — re-add "composition" or pass
# --backends fq,composition to run both head-to-head again).
BACKENDS="${BENCH_BACKENDS:-fq}"
SKIP_RELOAD=0; SKIP_REGEN=0; SKIP_OBSERVER=0; SKIP_RUN=0
MAX_ATTEMPTS=1; CONTINUE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --model)        MODEL="$2"; shift 2 ;;
    --backends)     BACKENDS="$2"; shift 2 ;;
    --max-attempts) MAX_ATTEMPTS="$2"; shift 2 ;;
    --skip-reload)  SKIP_RELOAD=1; shift ;;
    --skip-regen)   SKIP_REGEN=1; shift ;;
    --skip-observer) SKIP_OBSERVER=1; shift ;;
    --skip-run)     SKIP_RUN=1; shift ;;
    --continue)     CONTINUE="--continue"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

COMPOSE="docker compose -f bench/docker-compose.bench.yml"
UV="uv run --offline --with rdflib --with pyyaml"
mkdir -p "$BENCH_LIVE_DIR"

echo "=================================================================="
echo " BENCH_LIVE_DIR  = $BENCH_LIVE_DIR   (observer + run_bench share this)"
echo " BENCH_TESTCASES = $BENCH_TESTCASES"
echo " model = $MODEL    backends = $BACKENDS"
echo "=================================================================="

if [ "$SKIP_RELOAD" = 0 ]; then
  echo "=== STEP 1: reload bench Fuseki + MCP (rebuilt data + fresh skills) ==="
  # --force-recreate so the MCP re-reads bench/skills/*.md; RELOAD=1 re-parses the
  # rebuilt futuram.ttl into TDB2. Recreate MCP too so new/edited skills go live.
  RELOAD=1 $COMPOSE up -d --force-recreate bench-materialize bench-fuseki bench-mcp
  echo "  waiting for Fuseki health..."
  for i in $(seq 1 60); do
    curl -sf "http://localhost:47040/query/sparql?query=ASK%7B%7D" >/dev/null 2>&1 && { echo "  Fuseki up."; break; }
    sleep 5
  done
else
  echo "=== STEP 1 skipped (--skip-reload) ==="
fi

# STEP 2 (removed): the testcases are NO LONGER generated from the live endpoint.
# bench/testcases/domain.yaml is the benchmark — the 10 NL Q&A + the SI-5/6/7
# competency questions, with questions. It is a hand-maintained artifact and must
# never be auto-overwritten from data. (--skip-regen is kept as a no-op for compat.)
echo "=== STEP 2: using committed SI benchmark $BENCH_TESTCASES (not regenerated) ==="

if [ "$SKIP_OBSERVER" = 0 ]; then
  echo "=== STEP 3: observer on :3737 pointed at \$BENCH_LIVE_DIR ==="
  if curl -sf -o /dev/null http://localhost:3737 2>/dev/null; then
    echo "  observer already up. NOTE: if it was started with a DIFFERENT"
    echo "  BENCH_LIVE_DIR, restart it so it reads $BENCH_LIVE_DIR:"
    echo "    (cd bench/observer && BENCH_LIVE_DIR=$BENCH_LIVE_DIR BENCH_TESTCASES=$BENCH_TESTCASES npm run dev)"
  else
    echo "  starting observer (background)..."
    ( cd bench/observer && BENCH_LIVE_DIR="$BENCH_LIVE_DIR" BENCH_TESTCASES="$BENCH_TESTCASES" \
        nohup npm run dev >/tmp/bench-observer.log 2>&1 & )
    echo "  observer starting -> http://localhost:3737  (log: /tmp/bench-observer.log)"
  fi
else
  echo "=== STEP 3 skipped (--skip-observer) ==="
fi

if [ "$SKIP_RUN" = 0 ]; then
  echo "=== STEP 4: run the bench into \$BENCH_LIVE_DIR ==="
  uv run --offline --with rdflib --with pyyaml --with ollama --with mcp --with requests \
    python bench/run_bench.py "$BENCH_TESTCASES" \
    --backends "$BACKENDS" --model "$MODEL" --skills --max-attempts "$MAX_ATTEMPTS" \
    --live-dir "$BENCH_LIVE_DIR" $CONTINUE
  echo "  done. watch/inspect at http://localhost:3737"
else
  echo "=== STEP 4 skipped (--skip-run) ==="
fi

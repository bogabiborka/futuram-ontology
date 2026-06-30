#!/usr/bin/env bash
# Time-aware fq-vs-composition bench, against the REAL CSV ELV dataset.
#
# Runs the four staged steps end-to-end:
#   1. rebuild the futuram datasource (real ELV CSV, full 1980-2050 span) — the
#      composition + derived fq view, both /composition and /query datasets;
#   2. bring up the bench docker stack (sparql-llm MCP, Fuseki, qdrant, VoID,
#      indexer) pointed at the futuram data;
#   3. regenerate the data-derived test cases (incl. the TIME cases: year-scoped
#      breakdown, year-vs-mean, cross-year, and time x Metal-Wheel/ChEBI);
#   4. run the bench on a LOCAL Ollama model over BOTH backends.
#
# Model: qwen3-coder:30b (local). Years: 1980 / 2010 / 2030 (real spread).
#
# Usage:  bash bench/run_time_bench.sh [--model <tag>] [--skip-build] [--skip-up]
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="qwen3-coder:30b"
SKIP_BUILD=0
SKIP_UP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --skip-up) SKIP_UP=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

UV="uv run --offline --with rdflib --with pyyaml --with openpyxl"
COMPOSE="docker compose -f bench/docker-compose.bench.yml"
MCP="http://localhost:47898/"

echo "=== STEP 1: build the real ELV CSV dataset into the served futuram dirs ==="
echo "    (all drivetrains, full 1980-2050 span -> fuseki/futuram/data/{composition,query})"
if [ "$SKIP_BUILD" = 0 ]; then
  PYTHONPATH=src:tests uv run --offline --with rdflib --with pyyaml --with owlrl \
    --with pyshacl --with openpyxl python tests/build_instances.py futuram
else
  echo "  (skipped --skip-build)"
fi

echo "=== STEP 2: bring up the bench docker stack (MCP, Fuseki, VoID, indexer) ==="
if [ "$SKIP_UP" = 0 ]; then
  $COMPOSE up -d --build
  echo "  waiting for the bench Fuseki + MCP to be healthy..."
  for i in $(seq 1 60); do
    if curl -sf "http://localhost:47040/query/sparql?query=ASK%7B%7D" >/dev/null 2>&1; then
      echo "  Fuseki up."; break
    fi
    sleep 5
  done
else
  echo "  (skipped --skip-up)"
fi

echo "=== STEP 3: using the committed SI benchmark (NOT regenerated) ==="
echo "  bench/testcases/domain.yaml = the 10 NL Q&A + SI-5/6/7, questions+answers"
echo "  taken 1:1 from the paper SI; hand-maintained, never auto-overwritten."
grep -E "^\s+- id:" bench/testcases/domain.yaml | sed 's/^/    /'

echo "=== STEP 4: run the bench on $MODEL over both backends ==="
uv run --with ollama --with mcp --with pyyaml --with rdflib \
  python bench/run_bench.py \
    --model "$MODEL" \
    --backends fq,composition \
    --testcases bench/testcases/domain.yaml \
    --mcp "$MCP"

echo "=== DONE ==="

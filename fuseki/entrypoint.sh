#!/usr/bin/env bash
# Load mounted TTL into the persistent TDB2 store at startup, then serve.
#
# Source TTL is mounted at /staging/{composition,query} (compose binds
# fuseki/<instance>/data there, read-only). The TDB2 store lives on a named
# volume at /fuseki-base/databases. We load a dataset only when its store is
# empty (first run) or RELOAD=1 — so a normal restart is instant, and picking up
# new data is just `RELOAD=1 docker compose up` (or restart with RELOAD set).
set -euo pipefail

DB=/fuseki-base/databases
STAGING=/staging
# This image (secoresearch/fuseki, Alpine) ships the TDB2 bulk loader as
# tdb2.xloader under /jena/bin (NOT tdb2.tdbloader). Same CLI: --loc <dir> FILE...
LOADER="$(command -v tdb2.xloader || echo /jena/bin/tdb2.xloader)"

load_dataset() {
  local name="$1"               # composition | query
  local loc="$DB/$name"
  local src="$STAGING/$name"
  local done_marker="$DB/.$name.loaded"

  # Skip if already loaded (marker present) and not forced.
  if [ "${RELOAD:-0}" != "1" ] && [ -f "$done_marker" ]; then
    echo "[entrypoint] $name already loaded (marker present, RELOAD!=1) — skipping."
    return 0
  fi

  echo "[entrypoint] loading $name from $src ..."
  # tdb2.xloader REQUIRES --loc to NOT already exist; remove it entirely so the
  # loader creates a clean store (don't pre-mkdir).
  rm -rf "$loc" "$done_marker"
  # composition may be nested (futuram: composition/<drivetrain>/<bucket>.ttl);
  # query is flat. find handles both. ONE dir per dataset — the dataset's full
  # contents (statements + TBox + ChEBI + criticality + bridges) live in $src.
  local search="$src"
  local files
  # shellcheck disable=SC2086
  files="$(find $search -name '*.ttl' 2>/dev/null || true)"
  if [ -n "$files" ]; then
    local tmp="$DB/.xload-tmp-$name"
    rm -rf "$tmp"; mkdir -p "$tmp"
    # shellcheck disable=SC2086
    "$LOADER" --loc="$loc" --tmpdir="$tmp" $files
    rm -rf "$tmp"
    touch "$done_marker"
  else
    echo "[entrypoint] WARN: no .ttl found under $src (empty dataset)"
    mkdir -p "$loc"                          # empty dataset still needs the dir
    touch "$done_marker"
  fi
}

# Composition: bulk-load into the default graph (no unionDefaultGraph there).
load_dataset composition

# Query: the dataset uses tdb2:unionDefaultGraph, so the served default view is
# the UNION of NAMED graphs (base + per-class edit changefiles). xloader can only
# write the default graph, which union-mode then HIDES — so we do NOT xload the
# query base. Instead we create an empty query store and, after Fuseki starts,
# GSP-PUT the base TTL into a NAMED graph (urn:futuram:base) so it joins the union.
QBASE_MARKER="$DB/.query.basegraph.loaded"
if [ "${RELOAD:-0}" = "1" ] || [ ! -f "$DB/query/.ready" ]; then
  rm -rf "$DB/query" "$QBASE_MARKER"
  mkdir -p "$DB/query"; touch "$DB/query/.ready"
fi
chmod -R 0777 "$DB" || true

echo "[entrypoint] starting Fuseki ..."
/jena-fuseki/fuseki-server --config=/fuseki-base/config.ttl &
FUSEKI_PID=$!

# Load the query data into a named graph once Fuseki is reachable (idempotent).
# The dataset uses unionDefaultGraph, so the served default view is the UNION of
# named graphs; xloader can only write the (hidden) default graph, so we GSP-PUT
# every query TTL into the named graph urn:futuram:base. ONE dir now — the full fq
# view (futuram.ttl + ChEBI module/bridge + criticality) lives under $STAGING/query.
if [ "${RELOAD:-0}" = "1" ] || [ ! -f "$QBASE_MARKER" ]; then
  base_files="$(find "$STAGING/query" -name '*.ttl' 2>/dev/null || true)"
  if [ -n "$base_files" ]; then
    echo "[entrypoint] waiting for Fuseki to load query base ..."
    # This image (busybox Alpine) has wget, NOT curl.
    for i in $(seq 1 60); do
      if wget -q -O- "http://localhost:3030/query/sparql?query=ASK%7B%7D" >/dev/null 2>&1; then
        for f in $base_files; do
          if wget -q -O- --header="Content-Type: text/turtle" \
               --post-file="$f" \
               "http://localhost:3030/query/data?graph=urn:futuram:base" >/dev/null 2>&1; then
            echo "[entrypoint]   loaded $f -> urn:futuram:base"
          else
            echo "[entrypoint]   WARN: failed to POST $f"
          fi
        done
        touch "$QBASE_MARKER"
        break
      fi
      sleep 2
    done
  fi
fi

wait "$FUSEKI_PID"

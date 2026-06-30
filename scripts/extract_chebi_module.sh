#!/usr/bin/env bash
# Build the ChEBI + Metal-Wheel overlay loaded into the futuram /query graph.
#
# Self-contained: reads ONLY from ontology/sources/ (the inputs were copied out
# of legacy/ so this repo no longer depends on the legacy tree).
#
#   ontology/sources/chebi/chebi_core.owl.gz   full ChEBI core (compressed)
#   ontology/sources/chebi/chebi-term-file.txt seed terms (~99) for the module
#   ontology/sources/metal-wheel/*.ttl         TBox + ABox + criticality overlay
#
# ChEBI: ROBOT `extract --method STAR` pulls a self-contained ~96KB module
# around the seed terms (plus their superclasses) — we never load full ChEBI
# (345MB decompressed) into the served graph. Metal-Wheel TTLs are copied as-is.
#
# Output goes into the futuram query dir, which the Fuseki entrypoint loads:
#   fuseki/futuram/data/query/chebi-module.ttl
#   fuseki/futuram/data/query/metalwheel-{tbox,abox,criticality}.ttl
#
# The ChEBI STAR extract is the only slow step (decompress 345MB + ROBOT). It is
# SKIPPED when chebi-module.ttl already exists and is newer than its inputs
# (the term-file and the .gz). Set FORCE_CHEBI=1 to rebuild unconditionally
# (e.g. after editing the term-file in a way mtime didn't catch). The cheap
# Metal-Wheel copies always run.
#
# Newer JDKs cap JAXP entity sizes; ChEBI trips that limit, so we raise it.
set -euo pipefail

ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SRC="${ONTOLOGY_SOURCES:-$ROOT/ontology/sources}"
OUT_DIR="${OUT_DIR:-$ROOT/fuseki/futuram/data/query}"

CHEBI_GZ="$SRC/chebi/chebi_core.owl.gz"
TERM_FILE="$SRC/chebi/chebi-term-file.txt"
CHEBI_OWL="${CHEBI_GZ%.gz}"
OUT_TTL="$OUT_DIR/chebi-module.ttl"

mkdir -p "$OUT_DIR"

# --- Metal-Wheel overlay: copy the static TTL sources straight in. -----------
copy_mw() { # <src-basename> <dest-basename>
  local s="$SRC/metal-wheel/$1" d="$OUT_DIR/$2"
  [ -f "$s" ] || { echo "[overlay] ERROR: missing $s" >&2; exit 1; }
  cp "$s" "$d"; echo "[overlay] $1 -> $d"
}
copy_mw "MetalWheel-TBox.ttl"               "metalwheel-tbox.ttl"
copy_mw "MetalWheel-ABox.ttl"               "metalwheel-abox.ttl"
copy_mw "MetalWheel-Criticality-ABox.ttl"   "metalwheel-criticality.ttl"

# --- ChEBI module: ROBOT STAR extract from the (decompressed) core. ----------
# Skip the slow extract when the module is present and up-to-date vs its inputs.
if [ "${FORCE_CHEBI:-0}" != "1" ] && [ -f "$OUT_TTL" ] \
   && [ "$OUT_TTL" -nt "$TERM_FILE" ] \
   && { [ ! -f "$CHEBI_GZ" ] || [ "$OUT_TTL" -nt "$CHEBI_GZ" ]; }; then
  echo "[chebi-extract] $OUT_TTL up-to-date (newer than term-file + source) — skipping ROBOT extract. Set FORCE_CHEBI=1 to rebuild."
  echo "[chebi-extract] overlay ready."
  exit 0
fi

if [ ! -f "$CHEBI_GZ" ] && [ ! -f "$CHEBI_OWL" ]; then
  echo "[chebi-extract] ERROR: neither $CHEBI_GZ nor $CHEBI_OWL present" >&2
  exit 1
fi
if [ ! -f "$CHEBI_OWL" ] || [ "$CHEBI_GZ" -nt "$CHEBI_OWL" ]; then
  echo "[chebi-extract] decompressing ChEBI core (keeping .gz) ..."
  gunzip -kf "$CHEBI_GZ"
fi

echo "[chebi-extract] ROBOT STAR extract -> $OUT_TTL"
export ROBOT_JAVA_ARGS="${ROBOT_JAVA_ARGS:--Xmx8g} \
  -Djdk.xml.maxGeneralEntitySizeLimit=0 \
  -Djdk.xml.totalEntitySizeLimit=0 \
  -Djdk.xml.entityExpansionLimit=0"
robot extract \
  --method STAR \
  --input "$CHEBI_OWL" \
  --term-file "$TERM_FILE" \
  --output "$OUT_TTL"

echo "[chebi-extract] done: $(wc -l < "$OUT_TTL") lines -> $OUT_TTL"

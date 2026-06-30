# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///
"""Close the Rule-S gap (audit_hierarchy_abox.py) by stamping a
futuram:hasAggregationStrategy on every statement-less taxonomy class
(P/C -> YearSliceMean, Material -> MassWeightedRollup; Elements exempt). Idempotent.

Usage:  uv run scripts/enrich_hierarchy_abox.py [--dry-run]
"""
from __future__ import annotations

import argparse
import pathlib
from collections import defaultdict

from rdflib import Graph, Namespace, RDF, RDFS, OWL

ROOT = pathlib.Path(__file__).resolve().parent.parent
HIERARCHY = ROOT / "ontology" / "tbox" / "futuram-hierarchy.ttl"

FUT = Namespace("https://www.purl.org/futuram#")
LEVEL_ROOTS = ("Product", "Component", "Material", "Element")
STRATEGY_BY_LEVEL = {
    "Product": FUT.YearSliceMeanStrategy,
    "Component": FUT.YearSliceMeanStrategy,
    "Material": FUT.MassWeightedRollupStrategy,
}


def _local(iri):
    return str(iri).rsplit("#", 1)[-1]


def roots_of(cls, sup, memo):
    if cls in LEVEL_ROOTS:
        return {cls}
    if cls in memo:
        return memo[cls]
    memo[cls] = set()
    found = set()
    for parent in sup.get(cls, ()):
        found |= roots_of(parent, sup, memo)
    memo[cls] = found
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    g = Graph()
    g.parse(HIERARCHY, format="turtle")

    sup = defaultdict(set)
    for s, o in g.subject_objects(RDFS.subClassOf):
        sup[_local(s)].add(_local(o))

    memo = {}
    added = defaultdict(int)
    for s in sorted(set(g.subjects(RDF.type, OWL.Class))):
        if not str(s).startswith(str(FUT)):
            continue
        cls = _local(s)
        if cls in LEVEL_ROOTS:
            continue
        roots = roots_of(cls, sup, memo)
        if len(roots) != 1:
            continue                      # structural anomaly: audit reports it
        strategy = STRATEGY_BY_LEVEL.get(next(iter(roots)))
        if strategy is None:              # Element: exempt
            continue
        if (s, FUT.hasAggregationStrategy, None) in g:
            continue                      # idempotent: keep what is declared
        g.add((s, FUT.hasAggregationStrategy, strategy))
        added[_local(strategy)] += 1

    for name, n in sorted(added.items()):
        print(f"+{n:4d} hasAggregationStrategy {name}")
    if not added:
        print("nothing to add (already enriched)")
        return
    if args.dry_run:
        print("dry run — file unchanged")
        return
    g.serialize(destination=HIERARCHY, format="turtle")
    print(f"rewrote {HIERARCHY.relative_to(ROOT)} ({len(g)} triples)")


if __name__ == "__main__":
    raise SystemExit(main())

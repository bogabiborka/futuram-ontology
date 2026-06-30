# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml"]
# ///
"""Golden-oracle surface: the shared definition of WHAT is frozen.

One source of truth for both scripts/freeze_oracle.py (writes/verifies
tests/expected/<version>/) and tests/test_golden_oracle.py (the pytest gate).
"""
from __future__ import annotations

import pathlib

from etl import TEST_INPUT, elv_csv

TESTS = pathlib.Path(__file__).resolve().parent
ROOT = TESTS.parent
YAML_DIR = TEST_INPUT                                  # etl/input/test
ONECAR_TTL = TESTS / "fixtures" / "26_onecar_real.ttl"
ELV_CSV = elv_csv("BEV")                               # etl/input/futuram
CSV_YEARS = (2025, 2026, 2027)          # small, representative multi-year slice

# the ACTIVE fixture set the suite runs against: v2 (migrated time-based target),
# verified against the v1 baseline — values preserved under both slice and base
# keys; 26-onecar-real's per-year slices are the one intended semantic change.
ACTIVE_VERSION = "v2"

REL_TOL = 1e-9
ABS_TOL = 1e-12


def expected_dir(version=None):
    return TESTS / "expected" / (version or ACTIVE_VERSION)


def iter_sources():
    """Yield (source_id, loader) for everything the golden oracle covers."""
    from oracle import fastchain
    for p in sorted(YAML_DIR.glob("*.yaml")):
        yield p.stem, lambda p=p: fastchain.SupplyChain.from_yaml(p)
    if ONECAR_TTL.exists():
        yield "rdf-26-onecar-real", _load_onecar_rdf
    if ELV_CSV.exists():
        yield "csv-elv-bev-2025-2027", _load_csv_slice


def _load_onecar_rdf():
    from oracle.from_graph import from_turtle    # oracle reference reads its OWN fastchain chain
    return from_turtle(ONECAR_TTL)


def _load_csv_slice():
    from etl import csv_to_rdf as X
    from chain_from_doc import chain_from_doc
    doc = X.transform(ELV_CSV, sid="elv_freeze", years=set(CSV_YEARS))
    return chain_from_doc(doc, label="csv-elv-bev-2025-2027")


def surface(sc):
    """The full deterministic oracle surface of one chain, JSON-shaped.
    coarse_fine's (whole, part) tuple keys become 'whole||part' strings."""
    elem_classes = sorted({n.cls for n in sc.nodes.values()
                           if n.level == "Element"})
    eiw = {}
    for whole, nd in sorted(sc.nodes.items()):
        if nd.level == "Element":
            continue
        row = {ec: sc.element_in_whole(whole, ec) for ec in elem_classes}
        eiw[whole] = {ec: v for ec, v in row.items() if v != 0.0}
    return {
        "aggregate": {use: sc.aggregate(use) for use in ("best", "lo", "hi")},
        "element_in_whole": eiw,
        "conservation": sc.conservation(),
        "coarse_fine": {f"{w}||{p}": v
                        for (w, p), v in sc.coarse_fine().items()},
    }

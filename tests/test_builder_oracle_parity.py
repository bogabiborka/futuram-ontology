# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl", "pytest", "openpyxl"]
# ///
"""builder == oracle parity: the builder's served fq: graph must carry the SAME
per-(whole class, element class) numbers the oracle computes independently, across
every golden source. The oracle is the REFERENCE — if red, the builder drifted.
"""
import sys

import pytest
from rdflib import Graph, RDF

import golden
from common.vocab import FQ, FUT
from builder import resolver

REL, ABS = golden.REL_TOL, golden.ABS_TOL

# the golden sources, by id (scenarios + oneCar RDF + CSV slice) — the same set
# test_golden_oracle freezes, so parity covers exactly what is ground-truthed.
SOURCES = dict(golden.iter_sources())


def _element_classes(sc):
    """The Element-level class localnames in the chain — the oracle's aggregate
    covers exactly these as constituents; parity is over the ELEMENT rows only
    (fq:contains also carries component/material-in-whole rows)."""
    return {n.cls for n in sc.nodes.values() if n.level == "Element"}


def _builder_rows(sc, element_classes):
    """The builder's served (whole_class, element_class) -> amount, from
    resolve_all over the chain's composition RDF, RESTRICTED to element
    constituents (fq:contains also carries component/material rows)."""
    g = Graph()
    resolver.resolve_all(sc, into=g)
    rows = {}
    for a in g.subjects(RDF.type, FQ.Amount):
        w = g.value(a, FQ.whole)
        e = g.value(a, FQ.constituent)
        amt = g.value(a, FQ.amount)
        if w is None or e is None or amt is None:
            continue
        if _ln(e) not in element_classes:
            continue                       # element rows only — see docstring
        rows[(_ln(w), _ln(e))] = float(amt)
    return rows


def _oracle_rows(sc):
    """The oracle ground truth as (whole_class, element_class) -> amount, from
    sc.aggregate('best') for every leaf AND ancestor class (equal leaf mean,
    equal-subclass parent mean). Zero amounts dropped (no builder row)."""
    rows = {}
    for cls, by_elem in sc.aggregate("best").items():
        for ec, amt in by_elem.items():
            if amt != 0.0:
                rows[(cls, ec)] = amt
    return rows


def _ln(iri):
    return str(iri).rsplit("#", 1)[-1]


@pytest.mark.parametrize("sid", sorted(SOURCES), ids=lambda s: s)
def test_builder_matches_oracle(sid):
    """For every (class, element) the oracle aggregates, the builder's served fq:
    amount equals the oracle's value. Coverage is asserted ONE WAY: the builder
    legitimately serves MORE rows (the unknownElement filler is excluded)."""
    sc = SOURCES[sid]()
    # unknownElement EXCLUDED from both surfaces: oracle = the measured remainder,
    # builder = the serving balance filler closing the level to 1.0. Both correct
    # for their layer, not meant to be equal — parity over NAMED elements only.
    unknown = _ln(FUT.unknownElement)
    oracle = {k: v for k, v in _oracle_rows(sc).items() if k[1] != unknown}
    builder = {k: v for k, v in _builder_rows(sc, _element_classes(sc)).items()
               if k[1] != unknown}

    # every oracle (class, element) is served by the builder...
    missing = sorted(k for k in oracle if k not in builder)
    assert not missing, f"{sid}: builder is missing oracle rows {missing[:8]}"

    # ...with the same value (the actual numeric parity).
    for key, want in oracle.items():
        got = builder[key]
        assert got == pytest.approx(want, rel=REL, abs=ABS), \
            f"{sid}: {key[0]} / {key[1]}: builder {got} != oracle {want}"


def test_parity_covers_every_golden_source():
    """No silent coverage loss: parity runs over exactly the golden source set."""
    assert set(SOURCES) == {p.stem for p in golden.expected_dir().glob("*.json")}, \
        "parity source set drifted from the golden fixture set"

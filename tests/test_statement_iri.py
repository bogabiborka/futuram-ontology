# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl", "pytest"]
# ///
"""Stable, content-hashed composition-statement IRIs (incremental-fq plan §0):
futuram:stmt_<sha1(...)> instead of positional ex:sN — identical content yields an
identical IRI (set-union idempotent), differing content a different (conflict)IRI."""
import sys
import pathlib


from rdflib import RDF

import scenarios
from oracle.supplychain import FUT


def _stmt_iris(g):
    # the content-addressed stmt_<hash> identity now lives on the per-edge
    # PartRelation (1:1 with a measurement), not the per-whole CompositionStatement.
    return set(g.subjects(RDF.type, FUT.PartRelation))


def test_statement_iri_is_content_stable():
    """The same chain serialised twice yields the SAME set of statement IRIs
    (content-addressed, not positional/run-dependent)."""
    sc = scenarios.ALL[sorted(scenarios.ALL)[0]]
    a = _stmt_iris(sc.to_graph())
    b = _stmt_iris(sc.to_graph())
    assert a == b, "statement IRIs are not stable across two serialisations"
    assert a, "no statement IRIs emitted"


def test_statement_iri_is_content_addressed():
    """Statement IRIs are content hashes, not the positional ex:sN scheme."""
    sc = scenarios.ALL[sorted(scenarios.ALL)[0]]
    iris = {str(s) for s in _stmt_iris(sc.to_graph())}
    assert not any(s.rstrip("0123456789").endswith("#s") or s.rstrip("0123456789").endswith("/s")
                   for s in iris), f"still positional ex:sN scheme: {sorted(iris)[:3]}"
    assert all("stmt_" in s for s in iris), \
        f"expected futuram:stmt_<hash> IRIs, got {sorted(iris)[:3]}"


def test_differing_value_changes_the_iri():
    """A statement with a different best value gets a DIFFERENT IRI (so a
    conflicting re-statement is detectable as a second IRI on the same w/p)."""
    sc = scenarios.ALL[sorted(scenarios.ALL)[0]]
    base = _stmt_iris(sc.to_graph())
    # perturb one statement's best value and re-serialise
    s0 = sc.stmts[0]
    orig = s0.best
    try:
        s0.best = orig + 0.123456
        s0.lo = s0.hi = s0.best
        perturbed = _stmt_iris(sc.to_graph())
    finally:
        s0.best = orig
        s0.lo = s0.hi = orig
    assert perturbed != base, "perturbing a value did not change any statement IRI"

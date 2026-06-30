# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "pytest"]
# ///
"""SHACL well-formedness for the projected fq: graph: hand-built tiny graphs that
composition-query-shapes.ttl must accept (well-formed) or reject (malformed),
including the class-only rule (an fq:contains subject must be a CLASS)."""
import sys
import pathlib


import pytest
from rdflib import Graph, Namespace, BNode, Literal, RDF, RDFS, OWL, XSD

from common import pipeline

FQ = Namespace("https://www.purl.org/futuram/query#")
FUT = Namespace("https://www.purl.org/futuram#")


def served(amount=0.4, low=0.3, high=0.5, *, element=True, whole=True,
           unit=True, contains_from=None,
           subject_is_class=True, level=True, extra_level=None):
    """Build a one-node served fq: graph for testing. Defaults are WELL-FORMED;
    each kwarg flips one aspect to a malformed variant (subject_is_class violates
    §3 class-only; level=False/extra_level break the subClassOf-to-level kind)."""
    g = Graph()
    a = BNode()
    g.add((a, RDF.type, FQ.Amount))
    if element:
        g.add((a, FQ.constituent, FUT.Copper))
    if whole:
        g.add((a, FQ.whole, FUT.elvBEV))
    if level:
        g.add((FUT.Copper, RDFS.subClassOf, FUT.Element))
    if extra_level is not None:
        g.add((FUT.Copper, RDFS.subClassOf, extra_level))
    g.add((a, FQ.amount, Literal(amount, datatype=XSD.double)))
    if unit:
        g.add((a, FQ.unit, Literal("kg/kg")))
    if low is not None:
        g.add((a, FQ.amountLow, Literal(low, datatype=XSD.double)))
    if high is not None:
        g.add((a, FQ.amountHigh, Literal(high, datatype=XSD.double)))
    if contains_from is not None:
        if subject_is_class:
            g.add((contains_from, RDF.type, OWL.Class))
        else:
            g.add((contains_from, RDF.type, FUT.elvBEV))   # an instance
        g.add((contains_from, FQ.contains, a))
    return g


def conforms(g):
    return pipeline.validate_served(g).conforms


# ===========================================================================
# Well-formed nodes conform
# ===========================================================================

def test_wellformed_amount_conforms():
    assert conforms(served())


def test_point_amount_no_interval_conforms():
    """A degenerate point estimate (no low/high) is well-formed."""
    assert conforms(served(low=None, high=None))


def test_full_uncertainty_node_conforms():
    """An amount with the full uncertainty surface (empirical distribution)
    still conforms."""
    g = served()
    a = next(g.subjects(RDF.type, FQ.Amount))
    dist = BNode()
    g.add((dist, RDF.type, FQ.Empirical))
    g.add((a, FQ.distribution, dist))
    assert conforms(g)


# ===========================================================================
# Malformed nodes are rejected
# ===========================================================================

def test_missing_element_rejected():
    assert not conforms(served(element=False))


def test_missing_whole_rejected():
    assert not conforms(served(whole=False))


def test_missing_unit_rejected():
    assert not conforms(served(unit=False))


def test_missing_level_rejected():
    """A constituent with NO rdfs:subClassOf edge to a level class is rejected —
    its kind would be unqueryable (fq:level is retired; the ontology is the
    only kind marker)."""
    assert not conforms(served(level=False))


def test_two_levels_rejected():
    """A constituent claiming TWO level classes is rejected — the four levels
    are owl:AllDisjointClasses; this is exactly the pureCu-as-Element bug the
    retired fq:level marker could not catch."""
    assert not conforms(served(extra_level=FUT.Material))


def test_low_above_amount_rejected():
    assert not conforms(served(amount=0.4, low=0.6, high=0.5))


def test_amount_above_high_rejected():
    assert not conforms(served(amount=0.6, low=0.3, high=0.5))


def test_parametric_distribution_rejected():
    """A served distribution must be fq:Empirical, never a parametric claim."""
    g = served()
    a = next(g.subjects(RDF.type, FQ.Amount))
    dist = BNode()
    g.add((dist, RDF.type, FUT.NormalDistribution))   # not fq:Empirical
    g.add((a, FQ.distribution, dist))
    assert not conforms(g)


# ===========================================================================
# Class-only rule (§3) — the keystone shape check
# ===========================================================================

def test_contains_from_class_conforms():
    """An fq:contains edge whose subject is a CLASS is well-formed."""
    assert conforms(served(contains_from=FUT.elvBEV, subject_is_class=True))


def test_contains_from_instance_rejected():
    """An fq:contains edge whose subject is an INSTANCE violates the class-only
    rule of the fq: view and must be rejected."""
    assert not conforms(served(contains_from=FUT.someCarInstance,
                               subject_is_class=False))

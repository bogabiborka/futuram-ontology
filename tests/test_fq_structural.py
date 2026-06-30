# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl", "pytest"]
# ///
"""Self-describing fq view: every served aggregate class carries structural edges to
what it rolls up (rdfs:subClassOf, fq:sliceOf, fq:sliceAxis) so the incremental rollup
runs on the served graph ALONE; MC is POINTERS (fq:derivedFrom...) for on-demand compute.
"""
import pathlib
import sys


import pytest
from rdflib import Namespace, RDFS, RDF, OWL, Literal

from builder import resolver
import scenarios
from oracle.supplychain import FUT

FQ = Namespace("https://www.purl.org/futuram/query#")

# 26_onecar_real has a base elvBEV with year slices elvBEV_Y2010 / elvBEV_Y2011
# (a real YearSliceMean aggregate over its slices); the slices in turn sit over
# instance product classes — a genuine multi-stage aggregate chain.
AGG_SID = "26_onecar_real"


@pytest.fixture(scope="module")
def view():
    sc = scenarios.ALL[AGG_SID]
    # the resolver reads the composition RDF graph directly (RDF in -> RDF out);
    # the statement IRIs it points at are the content-hashed ones in that graph.
    g = sc.to_graph()
    return g, resolver.resolve_all(g)


def test_structural_edges_emitted(view):
    """A served year slice carries rdfs:subClassOf <base> AND the generic slice
    edge fq:sliceOf <base> + fq:sliceAxis YearSliceMeanStrategy — so the rollup
    runs on the fq graph alone, not just subClassOf <Level>."""
    rt, g = view
    base = FUT["elvBEV"]
    sliced = False
    for slc in (FUT["elvBEV_Y2010"], FUT["elvBEV_Y2011"]):
        if (slc, RDF.type, OWL.Class) not in g:
            continue
        sliced = True
        assert (slc, RDFS.subClassOf, base) in g, \
            f"{slc} not rdfs:subClassOf its base on the view"
        assert (slc, FQ.sliceOf, base) in g, \
            f"{slc} missing fq:sliceOf on the view"
        assert (slc, FQ.sliceAxis, FUT.YearSliceMeanStrategy) in g, \
            f"{slc} missing fq:sliceAxis on the view"
    assert sliced, "no elvBEV year slices were served — fixture assumption broke"


def test_aggregate_carries_mc_pointers(view):
    """The base aggregate elvBEV has fq:mcAvailable true + fq:derivedFrom (its
    subclass slices) + fq:derivedFromStatement, and NO materialised MC band on
    its amount rows (default with_mc=False)."""
    rt, g = view
    base = FUT["elvBEV"]
    assert (base, FQ.mcAvailable, Literal(True)) in g, "aggregate lacks fq:mcAvailable"
    derived = set(g.objects(base, FQ.derivedFrom))
    assert any("elvBEV_Y" in str(d) for d in derived), \
        f"fq:derivedFrom does not point at the year slices: {derived}"
    stmts = set(g.objects(base, FQ.derivedFromStatement))
    assert stmts, "fq:derivedFromStatement pointers missing"
    assert all("stmt_" in str(s) for s in stmts), f"non-statement derivedFrom: {stmts}"
    for a in g.objects(base, FQ.contains):
        assert (a, FQ.amountLow, None) not in g, "aggregate has a materialised MC band"
        assert (a, FQ.amountHigh, None) not in g


def test_leaf_class_is_not_flagged_as_aggregate(view):
    """A leaf product class (an instance class with no further subclasses) does
    NOT carry the aggregate MC pointers — only true aggregates defer MC."""
    rt, g = view
    # an instance product class under a base slice (a leaf): no fq:derivedFrom.
    leaves = [s for s in g.subjects(RDF.type, OWL.Class)
              if "_Y20" in str(s) and not set(g.objects(s, FQ.derivedFrom))]
    assert leaves, "expected at least one served leaf slice without derivedFrom"

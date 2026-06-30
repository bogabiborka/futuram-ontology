# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "pytest"]
# ///
"""Drivetrain slice dimension: cross-drivetrain shared component classes get a
drivetrain axis parallel to year via the GENERIC ValueAxisSlicer(DRIVETRAIN_AXIS),
derived from the graph's own markers — no axis identity passed in."""
import pathlib
import sys


import pytest
from rdflib import Graph, Namespace, RDF, RDFS, OWL, Literal, XSD

from builder.slicer import (ValueAxisSlicer, attach_value_strategy, compose,
                            YearSlicer, DRIVETRAIN_AXIS)

FUT = Namespace("https://www.purl.org/futuram#")
EX = Namespace("https://www.purl.org/futuram/example#")


def _mark_drivetrain(g, dt):
    """Author the GENERIC drivetrain value marker the ETL emits from
    productKeyLevel1: the drivetrain class carries futuram:sliceAxis
    DrivetrainMeanStrategy. This is the ONLY drivetrain signal the slicer keys on."""
    g.add((FUT[dt], RDF.type, OWL.Class))
    g.add((FUT[dt], FUT.sliceAxis, DRIVETRAIN_AXIS))


def _year_slice_class(g, base, year):
    """Mint a year-slice component class base_Y{year} in g (the shape the
    transformer/derivation emit): subClassOf base, referenceYear, and the generic
    year slice edge (sliceOf base + sliceAxis YearSliceMeanStrategy)."""
    c = FUT[f"{base}_Y{year}"]
    g.add((c, RDF.type, OWL.Class))
    g.add((c, RDFS.subClassOf, FUT.Component))
    g.add((c, RDFS.subClassOf, FUT[base]))
    g.add((c, FUT.referenceYear, Literal(year, datatype=XSD.int)))
    g.add((c, FUT.sliceOf, FUT[base]))
    g.add((c, FUT.sliceAxis, FUT.YearSliceMeanStrategy))
    return c


def _product_with_component(g, dt, base, year, n_instances=2):
    """A minimal MERGED-graph fragment: a product instance typed into drivetrain
    class `dt`, holding a CompositionStatement with `n_instances` components typed
    into base_Y{year}; the component reaches `dt` through its part-of ROOT."""
    cls = _year_slice_class(g, base, year)
    prod = EX[f"{dt}_product_{year}"]
    g.add((prod, RDF.type, FUT[dt]))
    # grouped shape: ONE CompositionStatement per whole (prod), N PartRelations.
    cs = EX[f"comp_{dt}_product_{year}"]
    g.add((cs, RDF.type, FUT.CompositionStatement))
    g.add((prod, FUT.hasCompositionStatement, cs))
    for i in range(n_instances):
        comp = EX[f"{dt}_{base}_{i}_{year}"]
        g.add((comp, RDF.type, cls))
        rel = EX[f"stmt_{dt}_{base}_{i}_{year}"]
        g.add((rel, RDF.type, FUT.PartRelation))
        g.add((cs, FUT.hasPartRelation, rel))
        g.add((rel, FUT.refersTo, comp))
    return cls


def test_value_slicer_detects_cross_drivetrain_sharing():
    """A component base used under >= 2 drivetrains is SHARED (its instances get
    retyped per drivetrain); a single-drivetrain base is NOT. Sharing is derived
    from the graph — no shared-base list is passed in."""
    g = Graph()
    _mark_drivetrain(g, "elvBEV")
    _mark_drivetrain(g, "elvPetrol")
    # shared: elvGeneralComponents appears under BOTH drivetrains
    _product_with_component(g, "elvBEV", "elvGeneralComponents", 2026)
    _product_with_component(g, "elvPetrol", "elvGeneralComponents", 2026)
    # bev-only: elvBatteryPack appears under ONE drivetrain
    _product_with_component(g, "elvBEV", "elvBatteryPack", 2026)

    out = ValueAxisSlicer(DRIVETRAIN_AXIS).apply(g)

    # shared base: BOTH drivetrain leaves of the year slice exist
    yslice = FUT["elvGeneralComponents_Y2026"]
    assert (FUT["elvBEV_elvGeneralComponents_Y2026"], FUT.sliceOf, yslice) in out
    assert (FUT["elvPetrol_elvGeneralComponents_Y2026"], FUT.sliceOf, yslice) in out
    # single-drivetrain base: NOT retyped (no drivetrain leaf minted)
    assert not any(out.triples(
        (FUT["elvBEV_elvBatteryPack_Y2026"], None, None)))


def test_shared_slice_instances_retyped_and_parent_kept():
    """A shared slice's INSTANCES move to <drivetrain>_<comp>_Y<year>; that leaf is
    subClassOf the drivetrain-free <comp>_Y<year> and carries both the drivetrain
    and inherited year slice edges. The parent keeps its own year identity."""
    g = Graph()
    _mark_drivetrain(g, "elvBEV")
    _mark_drivetrain(g, "elvPetrol")
    _product_with_component(g, "elvBEV", "elvGeneralComponents", 2026)
    _product_with_component(g, "elvPetrol", "elvGeneralComponents", 2026)

    out = ValueAxisSlicer(DRIVETRAIN_AXIS).apply(g)
    leaf = FUT["elvBEV_elvGeneralComponents_Y2026"]
    parent = FUT["elvGeneralComponents_Y2026"]
    # instances retyped to the BEV leaf; none left on the bare year-slice parent
    assert any(out.triples((None, RDF.type, leaf)))
    assert not any(out.triples((None, RDF.type, parent)))
    # leaf wiring: drivetrain-axis slice edge of the year-slice parent
    assert (leaf, RDFS.subClassOf, parent) in out
    assert (leaf, FUT.sliceOf, parent) in out
    assert (leaf, FUT.sliceAxis, DRIVETRAIN_AXIS) in out
    # leaf also carries the inherited YEAR axis edge (sliceOf the timeless base)
    assert (leaf, FUT.sliceOf, FUT.elvGeneralComponents) in out
    assert (leaf, FUT.sliceAxis, FUT.YearSliceMeanStrategy) in out
    assert (leaf, FUT.referenceYear, Literal(2026, datatype=XSD.int)) in out
    # the drivetrain-free parent kept its own year-slice identity
    assert (parent, RDFS.subClassOf, FUT.elvGeneralComponents) in out
    assert (parent, FUT.referenceYear, Literal(2026, datatype=XSD.int)) in out


def test_attach_value_strategy():
    """The drivetrain-free year slice (the parent of a drivetrain-axis slice edge)
    is declared to aggregate by DrivetrainMeanStrategy. Generic in the axis IRI."""
    g = Graph()
    g.add((FUT["elvBEV_X_Y2026"], FUT.sliceOf, FUT["X_Y2026"]))
    g.add((FUT["elvBEV_X_Y2026"], FUT.sliceAxis, DRIVETRAIN_AXIS))
    attach_value_strategy(g, DRIVETRAIN_AXIS)
    assert (FUT["X_Y2026"], FUT.hasAggregationStrategy, DRIVETRAIN_AXIS) in g


def test_fleet_year_window_is_hard_clamped():
    """The ELV fleet build is hard-clamped to the full CSV span 1980-2050: a
    request never widens past it, though a caller may narrow WITHIN it. The
    historical tail (1980-2019) IS served (the real composition trend)."""
    from etl import buckets
    assert buckets._clamp_window(None, None) == (1980, 2050)        # default = full window
    assert buckets._clamp_window(1900, 2099) == (1980, 2050)        # widen attempt clamped
    assert buckets._clamp_window(1990, 2040) == (1990, 2040)        # narrow within = honoured
    assert buckets._clamp_window(2030, 2035) == (2030, 2035)        # narrow within = honoured


def test_value_slicer_is_noop_without_sharing():
    """With a single drivetrain (no base spans >= 2 values) the slicer mints no
    drivetrain leaves — sharing is graph-derived, so one drivetrain = no-op."""
    g = Graph()
    _mark_drivetrain(g, "elvBEV")
    _product_with_component(g, "elvBEV", "elvGeneralComponents", 2026)
    out = ValueAxisSlicer(DRIVETRAIN_AXIS).apply(g)
    assert not any(out.triples((FUT["elvBEV_elvGeneralComponents_Y2026"], None, None)))


def test_slicers_compose_order_is_nesting():
    """The generic slicers compose; ORDER = NESTING. Running drivetrain THEN year
    derives the full two-axis lattice (drivetrain leaf ⊑ year slice, year then mints
    ancestor year slices); both axes carry their own sliceOf/sliceAxis edge."""
    src = Graph()
    _mark_drivetrain(src, "elvBEV")
    _mark_drivetrain(src, "elvPetrol")
    _product_with_component(src, "elvBEV", "elvGeneralComponents", 2026)
    _product_with_component(src, "elvPetrol", "elvGeneralComponents", 2026)
    # the base's ancestor edge so YearSlicer has an ancestor to mint
    src.add((FUT.elvGeneralComponents, RDFS.subClassOf, FUT.elvComponentGroup))

    out = compose(src, [ValueAxisSlicer(DRIVETRAIN_AXIS), YearSlicer()])
    leaf = FUT["elvBEV_elvGeneralComponents_Y2026"]
    yslice = FUT["elvGeneralComponents_Y2026"]
    # drivetrain axis: leaf sliceOf the year slice @ DrivetrainMean
    assert (leaf, FUT.sliceOf, yslice) in out
    assert (leaf, FUT.sliceAxis, DRIVETRAIN_AXIS) in out
    # year axis: the year slice sliceOf its timeless base @ YearSliceMean, AND the
    # ancestor year slice was derived from the base's taxonomy parent
    assert (yslice, FUT.sliceOf, FUT.elvGeneralComponents) in out
    assert (yslice, FUT.sliceAxis, FUT.YearSliceMeanStrategy) in out
    anc = FUT["elvComponentGroup_Y2026"]
    assert (anc, FUT.sliceOf, FUT.elvComponentGroup) in out
    assert (anc, FUT.sliceAxis, FUT.YearSliceMeanStrategy) in out

# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl", "openpyxl", "pytest"]
# ///
"""End-to-end drivetrain dimension on real ELV data (BEV+HEV at 2030, the smallest
corpus with cross-drivetrain shared components): drivetrain-mean/year base, add_source
additivity, MC pointers. Marked slow (reads ELV CSVs); skipped if absent."""
import pathlib
from etl import elv_csv
import sys


import pytest
from rdflib import Graph, Namespace, RDF, RDFS, Literal

from etl import buckets
from etl import serve_corpus as B
from builder import store as S

ROOT = pathlib.Path(__file__).resolve().parent.parent
# BEV + HEV both carry an electric motor (Diesel/Petrol do not), so this pair
# exercises the drivetrain slice axis for the SHARED components in general AND for
# elvElectricMotor specifically (BEV's motor != HEV's motor, aggregated up).
CSV_BEV = elv_csv("BEV")
CSV_HEV = elv_csv("HEV")
YEAR = 2030                      # one specific year — the whole scenario

FQ = Namespace("https://www.purl.org/futuram/query#")
FUT = Namespace("https://www.purl.org/futuram#")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (CSV_BEV.exists() and CSV_HEV.exists()),
                       reason="ELV BEV/HEV CSVs not present"),
]


@pytest.fixture(scope="module")
def futuram_view(tmp_path_factory):
    """ONE specific scenario: BEV + Petrol at year 2030 only (width=1 -> one
    bucket per drivetrain). Built ONCE via the fast one-shot path. Returns
    (view, comp_dir)."""
    tmp = tmp_path_factory.mktemp("veh")
    comp = tmp / "composition"
    buckets.export_buckets(CSV_BEV, comp / "bev", width=1,
                           year_min=YEAR, year_max=YEAR, canonicalize=False)
    buckets.export_buckets(CSV_HEV, comp / "hev", width=1,
                           year_min=YEAR, year_max=YEAR, canonicalize=False)
    out = tmp / "futuram.ttl"
    B.serve_corpus(comp, out)
    return Graph().parse(out, format="turtle"), comp


def _rows(view):
    return {(str(view.value(a, FQ.whole)), str(view.value(a, FQ.constituent)),
             round(float(view.value(a, FQ.amount)), 6))
            for a in view.subjects(RDF.type, FQ.Amount)
            if view.value(a, FQ.whole) is not None
            and view.value(a, FQ.constituent) is not None
            and view.value(a, FQ.amount) is not None}


def _amount(g, whole, element):
    for a in g.subjects(RDF.type, FQ.Amount):
        if g.value(a, FQ.whole) == whole and g.value(a, FQ.constituent) == element:
            return float(g.value(a, FQ.amount))
    return None


def _drivetrain_slice_edges(g, parent=None):
    """(leaf, parent) for every served DRIVETRAIN-axis slice edge: a generic
    fq:sliceOf whose fq:sliceAxis is DrivetrainMeanStrategy (no per-axis
    predicate). Optionally filtered to one parent."""
    out = []
    for leaf, _, p in g.triples((None, FQ.sliceOf, parent)):
        if (leaf, FQ.sliceAxis, FUT.DrivetrainMeanStrategy) in g:
            out.append((leaf, p))
    return out


def test_drivetrain_mean_and_year_base(futuram_view):
    """A drivetrain-free shared-component year slice <comp>_Y2030 equals the
    EQUAL MEAN of its drivetrain leaves elv<DT>_<comp>_Y2030 (DrivetrainMean)."""
    g, _ = futuram_view
    parents = {}
    for leaf, parent in _drivetrain_slice_edges(g):
        parents.setdefault(parent, set()).add(leaf)
    multi = {p: ls for p, ls in parents.items() if len(ls) >= 2}
    assert multi, "no shared component with >=2 drivetrain leaves was served"

    checked = 0
    for parent, leaves in multi.items():
        elems = None
        for leaf in leaves:
            le = {g.value(a, FQ.constituent) for a in g.objects(leaf, FQ.contains)}
            elems = le if elems is None else (elems & le)
        for e in (elems or set()):
            leaf_vals = [_amount(g, leaf, e) for leaf in leaves]
            if any(v is None for v in leaf_vals):
                continue
            expect = sum(leaf_vals) / len(leaf_vals)
            got = _amount(g, parent, e)
            if got is None:
                continue
            assert got == pytest.approx(expect, rel=1e-6, abs=1e-9), \
                f"{parent} {e}: {got} != drivetrain-mean {expect}"
            checked += 1
    assert checked, "no (parent, element) pair could be checked"


def test_electric_motor_is_a_drivetrain_aggregate(futuram_view):
    """elvElectricMotor is an aggregated component: slice elvElectricMotor_Y2030 is a
    DrivetrainMean bucket over per-drivetrain leaves (BEV's motor != HEV's) rolling up to
    the timeless base; content lives on leaves, the bucket carries only MC pointers."""
    g, _ = futuram_view
    base = FUT["elvElectricMotor"]
    yslice = FUT["elvElectricMotor_Y2030"]
    leaves = sorted(str(leaf).split("#")[-1]
                    for leaf, _ in _drivetrain_slice_edges(g, yslice))
    assert len(leaves) >= 2, f"motor year slice has <2 drivetrain leaves: {leaves}"
    assert {"elvBEV_elvElectricMotor_Y2030",
            "elvHEV_elvElectricMotor_Y2030"} <= set(leaves), leaves
    # the bucket aggregates by DrivetrainMean and rolls up to the timeless base
    assert (yslice, FQ.aggregationStrategy, FUT.DrivetrainMeanStrategy) in g
    assert (yslice, RDFS.subClassOf, base) in g
    # bucket value == equal mean of the drivetrain leaves, per shared element
    leaf_iris = [FUT[l] for l in leaves]
    elems = None
    for li in leaf_iris:
        le = {g.value(a, FQ.constituent) for a in g.objects(li, FQ.contains)}
        elems = le if elems is None else (elems & le)
    checked = 0
    for e in (elems or set()):
        vals = [_amount(g, li, e) for li in leaf_iris]
        if any(v is None for v in vals):
            continue
        got = _amount(g, yslice, e)
        if got is None:
            continue
        assert got == pytest.approx(sum(vals) / len(vals), rel=1e-6, abs=1e-9), \
            f"elvElectricMotor_Y2030 {e}: {got} != drivetrain-mean"
        checked += 1
    assert checked, "no motor (slice, element) pair could be checked"


def test_aggregate_carries_mc_pointers_on_futuram(futuram_view):
    """A drivetrain-mean year slice carries fq:mcAvailable + fq:derivedFrom (its
    drivetrain leaves) and NO materialised MC band — the pointer contract for
    on-demand MC (the actual MC math is covered by the scenario MC tests)."""
    g, _ = futuram_view
    parents = {p for _, p in _drivetrain_slice_edges(g)}
    assert parents
    ok = 0
    for p in parents:
        if (p, FQ.mcAvailable, Literal(True)) not in g:
            continue
        ok += 1
        assert set(g.objects(p, FQ.derivedFrom)), f"{p} has no fq:derivedFrom"
        for a in g.objects(p, FQ.contains):
            assert (a, FQ.amountLow, None) not in g
    assert ok, "no drivetrain-mean parent carried fq:mcAvailable"


def test_add_one_source_agrees_with_full(futuram_view, tmp_path):
    """add_source over one drivetrain source is value-identical to a one-shot
    derive of the same source (the additivity contract — the full equivalence
    gate is test_incremental_fq::test_increment_equals_full_derive)."""
    from etl import corpus
    from common import pipeline
    _, comp = futuram_view
    bev = comp / "bev"
    bucket = next(f for f in sorted(bev.glob("*.ttl"))
                  if f.name not in corpus._SKIP_NAMES)
    src = Graph().parse(str(bucket), format="turtle")
    shared = bev / pipeline.EM_SHARED_NAME
    if shared.exists():
        src.parse(str(shared), format="turtle")

    one_dir = tmp_path / "one"
    (one_dir / "bev").mkdir(parents=True)
    for f in bev.glob("*.ttl"):
        (one_dir / "bev" / f.name).write_bytes(f.read_bytes())
    full_out = tmp_path / "full.ttl"
    B.serve_corpus(one_dir, full_out)

    vp = tmp_path / "incr.ttl"
    store = S.Store()
    # No shared_bases seeding: the generic ValueAxisSlicer derives drivetrain
    # sharing from the graph's own futuram:sliceAxis markers at _finalise_store
    # (the builder sees only RDF — no source-layout state on the Store).
    S.add_source(vp, store, src, source_id="bev/" + bucket.stem)
    assert _rows(Graph().parse(vp, format="turtle")) == \
        _rows(Graph().parse(full_out, format="turtle"))

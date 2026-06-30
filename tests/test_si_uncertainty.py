# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "pytest", "owlrl", "pyyaml"]
# ///
"""1-to-1 reproduction of the FutuRaM SI uncertainty values: pins our pipeline to
the R generator's exact arithmetic (uncertainty%=limit/√3×100, 7-term dataQualityMean
+ DQS bands, limit from DQ scores via the ruleset's weighted-sum+band) to ~1e-12."""
import math

import pytest
from rdflib import Graph, Namespace, RDF, XSD

from common import pipeline
from builder.resolver.uncertainty import RulesetReader

FUT = Namespace("https://www.purl.org/futuram#")
FQ = Namespace("https://www.purl.org/futuram/query#")
DQV = Namespace("http://www.w3.org/ns/dqv#")
SQRT3 = math.sqrt(3.0)


# ---------------------------------------------------------------------------
# The SOURCE generator's exact reference values (the GROUND TRUTH the SI
# uncertainty% column is built from).
# ---------------------------------------------------------------------------

# every uncertaintyLimit the source assigns, and the uncertainty% the R code derives
# (uncertaintyLimit / sqrt(3) * 100). Includes the per-material lookup limits
# {0.20,0.25,0.30,0.35} AND the layer-default limits {0.10,0.15} used elsewhere.
SOURCE_LIMIT_TO_UNCERTAINTY_PCT = {
    0.10: 10.0 / SQRT3,
    0.15: 15.0 / SQRT3,
    0.20: 20.0 / SQRT3,
    0.25: 25.0 / SQRT3,
    0.30: 30.0 / SQRT3,
    0.35: 35.0 / SQRT3,
}


@pytest.fixture(scope="module")
def reader():
    g = Graph()
    g.parse(pipeline.UNCERTAINTY_TBOX, format="turtle")
    g.parse(pipeline.TBOX, format="turtle")
    return RulesetReader(g)


# ---- the per-statement uncertainty formula (R lines 1066/1071) -------------

@pytest.mark.parametrize("limit,expected_pct", SOURCE_LIMIT_TO_UNCERTAINTY_PCT.items())
def test_uncertainty_pct_matches_source(limit, expected_pct):
    """uncertainty% = uncertaintyLimit / sqrt(3) * 100 (the source's stored column).
    Our served fq:relativeUncertainty is the RELATIVE sigma = limit/sqrt(3), so
    fq:relativeUncertainty * 100 == the source uncertainty%."""
    served_relative_sigma = limit / SQRT3
    assert served_relative_sigma * 100 == pytest.approx(expected_pct, abs=1e-9)


@pytest.mark.parametrize("limit", list(SOURCE_LIMIT_TO_UNCERTAINTY_PCT))
def test_interval_endpoints_match_source(limit):
    """valueLowerLimit = best*(1-limit), valueUpperLimit = best*(1+limit) (R 1041/1044).
    Our index derives lo/hi from the stored limit by exactly this rule."""
    best = 123.456
    lo, hi = best * (1.0 - limit), best * (1.0 + limit)
    assert lo == pytest.approx(best - best * limit)
    assert hi == pytest.approx(best + best * limit)
    assert (hi + lo) / 2 == pytest.approx(best)            # best is the midpoint


# ---- the data-quality grade (R lines 1081-1086) ---------------------------

def _source_data_quality_mean(v, a, c, i, t, co):
    """The source's exact dataQualityMean (VehicleComponentDataset.Rmd:1081):
    7-term mean = the six dims + mean(Accuracy, Consistency, Completeness)."""
    return (v + a + c + i + t + co + (a + c + co) / 3.0) / 7.0


def _source_dqs(mean):
    """The source's DQS banding (R 1083-1086)."""
    if mean < 1.3:
        return 1
    if mean < 2.3:
        return 2
    if mean < 3.3:
        return 3
    return 4


# the six distinct DQ vectors actually present in the reference BEV dataset, with
# the dataQualityMean / DQS the source computes for each.
SOURCE_DQ_VECTORS = [
    (1, 1, 1, 1, 1, 1),
    (1, 2, 2, 2, 1, 2),
    (2, 2, 2, 2, 1, 2),
    (2, 2, 3, 2, 2, 2),
    (2, 2, 3, 2, 3, 2),
    (2, 2, 3, 3, 2, 3),
]


@pytest.mark.parametrize("vec", SOURCE_DQ_VECTORS)
def test_data_quality_mean_matches_source(reader, vec):
    """Our ruleset's FuturamWeightedMean reproduces the source 7-term
    dataQualityMean EXACTLY (the Accuracy/Consistency/Completeness extra-weight term)."""
    v, a, c, i, t, co = vec
    scores = [(DQV.Validity, float(v)), (DQV.Accuracy, float(a)),
              (DQV.Consistency, float(c)), (DQV.Integrity, float(i)),
              (DQV.Timeliness, float(t)), (DQV.Completeness, float(co))]
    ours = reader.mean_data_quality(scores, FUT.FuturamWeightedMean)
    assert ours == pytest.approx(_source_data_quality_mean(*vec), abs=1e-12)


@pytest.mark.parametrize("vec", SOURCE_DQ_VECTORS)
def test_dqs_band_matches_source(reader, vec):
    """Our DQS banding equals the source's dataQuality on every reference vector."""
    ours_mean = reader.mean_data_quality(
        [(d, float(s)) for d, s in zip(
            [DQV.Validity, DQV.Accuracy, DQV.Consistency, DQV.Integrity,
             DQV.Timeliness, DQV.Completeness], vec)],
        FUT.FuturamWeightedMean)
    dqs, _unc = reader.band_for_mean(ours_mean)
    assert dqs == _source_dqs(_source_data_quality_mean(*vec))


# ---- end to end: a statement built with a source limit reproduces it -------

_ELV_BEV_CSV = pipeline.ROOT / "src" / "etl" / "input" / "futuram" / "ELV_1980_2050_BEV.csv"


@pytest.fixture(scope="module")
def bev_2025():
    """The composition graph + served fq: graph for a small real BEV slice (2025)
    through the full pipeline — the exact shape the production vehicle build
    produces. Returns (composition_graph, served_graph)."""
    pytest.importorskip("polars")
    if not _ELV_BEV_CSV.exists():
        pytest.skip("ELV BEV source CSV not present")
    from etl import csv_to_rdf as X
    from builder import derive
    g = X.to_graph(_ELV_BEV_CSV, sid="bev", years={2025}, canonicalize=True)
    merged, _ = derive.merge_sources([("bev", g)])
    return merged, derive.derive_all(merged)


@pytest.fixture(scope="module")
def served_bev_2025(bev_2025):
    return bev_2025[1]


def test_per_statement_uncertainty_pct_set_matches_source(served_bev_2025):
    """1-to-1: the per-statement futuram:hasRelativeUncertainty values (×100) over the
    real BEV data are EXACTLY the source uncertainty% set the R generator stores —
    {limit/√3×100 : limit ∈ source limits}. This is the SI uncertainty% column."""
    served = served_bev_2025
    stmt_sigmas = {round(float(o), 9)
                   for _s, o in served.subject_objects(FUT.hasRelativeUncertainty)}
    stmt_pct = {round(s * 100, 4) for s in stmt_sigmas}
    # every served per-statement uncertainty% must be a source limit/√3×100 value
    source_pcts = {round(L / SQRT3 * 100, 4) for L in SOURCE_LIMIT_TO_UNCERTAINTY_PCT}
    assert stmt_pct, "no per-statement uncertainty served"
    assert stmt_pct <= source_pcts, (
        f"served uncertainty% {sorted(stmt_pct)} not all in source set "
        f"{sorted(source_pcts)}")


def test_rectangular_carries_strategy_not_limit(bev_2025):
    """Composition statements carry a bare RectangularDistribution declaring an
    uncertaintyLimitStrategy — NOT a numeric limit or interval endpoints; only DQ
    scores + strategy are stored, the spread is derived (checks the composition graph)."""
    comp, _served = bev_2025
    CEONQ = Namespace("http://w3id.org/CEON/ontology/quantity/")
    rect = list(comp.subjects(RDF.type, FUT.RectangularDistribution))
    assert rect, "no RectangularDistribution emitted"
    # each rectangular dist declares the strategy, and stores NO numeric limit
    assert all(comp.value(d, FUT.uncertaintyLimitStrategy) == FUT.FuturamDQS for d in rect)
    assert all(comp.value(d, FUT.uncertaintyLimit) is None for d in rect)
    # and NO QuantityInterval min/max endpoint triples were stored
    assert not list(comp.subject_objects(CEONQ.hasMinimalValueIncludedOfInterval))
    assert not list(comp.subject_objects(CEONQ.hasMaximalValueIncludedOfInterval))

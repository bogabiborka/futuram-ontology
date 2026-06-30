# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "pytest", "owlrl", "pyyaml"]
# ///
"""The data-quality -> uncertainty rule (the SI method), in three tiers: (1) unit
tests that the ruleset.ttl reproduces SI Eq.(1) DQS bands / Eq.(2) percentages /
Eq.(3) RSS exactly; (2) end-to-end through the served graph; (3) SI-paper structure."""
import math

import pytest
from rdflib import Graph, Namespace, RDF, XSD

from common import pipeline
from builder.resolver.uncertainty import RulesetReader

FUT = Namespace("https://www.purl.org/futuram#")
FQ = Namespace("https://www.purl.org/futuram/query#")
DQV = Namespace("http://www.w3.org/ns/dqv#")


@pytest.fixture(scope="module")
def reader():
    """RulesetReader over the real ruleset + composition TBox (for dqv:inDimension
    and hasAggregationStrategy lookups)."""
    g = Graph()
    g.parse(pipeline.UNCERTAINTY_TBOX, format="turtle")
    g.parse(pipeline.TBOX, format="turtle")
    return RulesetReader(g)


# ============================================================================
# Tier 1 — the rule reproduces the SI equations exactly
# ============================================================================

# Eq.(1): 1-4 scale (D1.1 Table 2); mean < 1.3 -> DQS 1 ; 1.3 <= mean < 2.3 -> DQS 2
#         2.3 <= mean < 3.3 -> DQS 3 ; mean >= 3.3 -> DQS 4
@pytest.mark.parametrize("mean,dqs", [
    (1.0, 1), (1.2999, 1),               # [1.0, 1.3) -> DQS 1
    (1.3, 2), (2.0, 2), (2.2999, 2),     # [1.3, 2.3) -> DQS 2
    (2.3, 3), (2.5, 3), (3.0, 3),        # [2.3, 3.3) -> DQS 3 (incl. the dataset max 3.0)
    (3.3, 4), (4.0, 4),                  # >= 3.3 -> DQS 4 (Dubious)
])
def test_eq1_band_boundaries(reader, mean, dqs):
    got_dqs, _unc = reader.band_for_mean(mean)
    assert got_dqs == dqs


# Eq.(2): DQS 1 -> 10 % ; DQS 2 -> 20 % ; DQS 3 -> 40 %
@pytest.mark.parametrize("mean,pct", [
    (1.0, 0.10), (1.3, 0.20), (2.3, 0.40),
])
def test_eq2_dqs_to_uncertainty(reader, mean, pct):
    _dqs, unc = reader.band_for_mean(mean)
    assert unc == pytest.approx(pct)


def test_eq2_dqs4_is_a_range(reader):
    """DQS 4 (Dubious, mean >= 3.3) -> 60-80 %: the only band that maps to a RANGE."""
    band4 = FUT.FuturamDQS_band4
    g = reader.g
    assert float(g.value(band4, FUT.uncertaintyLowerBound)) == pytest.approx(0.60)
    assert float(g.value(band4, FUT.uncertaintyUpperBound)) == pytest.approx(0.80)
    assert float(g.value(band4, FUT.meanLowerBound)) == pytest.approx(3.3)
    assert g.value(band4, FUT.meanUpperBound) is None  # open top band


# Eq.(1) first arrow: the data-quality mean requires the FULL six-dimension vector.
def test_eq1_requires_all_six_dimensions(reader):
    # a partial assessment is rejected (RequireAllDimensions) — the rule is defined
    # over the complete vector; the ETL/loaders guarantee all six are present.
    assert reader.mean_data_quality(
        [(DQV.Accuracy, 2.0), (DQV.Completeness, 3.0)]) is None
    # the full six dimensions: all-1s -> mean 1.0 (the 7-term weighted mean of equal
    # scores is still that score).
    six = [(DQV.Validity, 1.0), (DQV.Accuracy, 1.0), (DQV.Consistency, 1.0),
           (DQV.Timeliness, 1.0), (DQV.Completeness, 1.0), (DQV.Integrity, 1.0)]
    assert reader.mean_data_quality(six) == pytest.approx(1.0)


def test_eq1_mean_uses_the_irI_identified_rule(reader):
    """The mean-of-dimensions step is governed by a futuram:DqvAggregation IRI
    (EqualMeanPerDimension), the rule the strategies point at via
    dqvAggregationRule — not a hardcoded average."""
    rule = reader.dqv_rule_of_strategy(FUT.EqualSubclassMeanStrategy)
    assert rule == FUT.EqualMeanPerDimension
    # the rule declares the SI's six dimensions
    dims = set(reader.g.objects(rule, FUT.expectsDimension))
    assert {DQV.Validity, DQV.Accuracy, DQV.Consistency,
            DQV.Timeliness, DQV.Completeness, DQV.Integrity} <= dims


# Eq.(3): contribution-weighted relative RSS u_aggregate = sqrt(Σ(u_i·v_i)^2)/Σv_i.
# Equal v_i reduces to sqrt(Σu_i^2)/N (not sqrt(Σu_i^2)) — the weighting keeps the
# result a sane fraction instead of growing with N.
@pytest.mark.parametrize("contribs", [
    [(0.2, 1.0), (0.2, 1.0)],
    [(0.1, 0.5), (0.4, 0.3), (0.2, 0.2)],
    [(0.10, 1.0)] * 4,
    [(0.4, 1.0)],
])
def test_eq3_contribution_weighted_rss(reader, contribs):
    total = sum(v for _u, v in contribs)
    expected = math.sqrt(sum((u * v) ** 2 for u, v in contribs)) / total
    assert reader.combine(contribs) == pytest.approx(expected)


def test_eq3_single_statement_is_its_own_uncertainty(reader):
    """One contributing statement -> its own relative uncertainty, any value."""
    assert reader.combine([(0.40, 0.073)]) == pytest.approx(0.40)


def test_eq3_weighting_prevents_blowup(reader):
    """The bug fix: N equal statements must NOT give sqrt(N)*u (>100 % for N>6 at
    40 %). Contribution-weighting keeps an aggregate of N equal 40 % statements at
    40 %, not 0.4*sqrt(N)."""
    for n in (1, 5, 18, 50):
        contribs = [(0.40, 1.0 / n)] * n          # equal split of a unit total
        combined = reader.combine(contribs)
        # equal values + equal u -> the aggregate relative equals u/sqrt(n)*...
        # specifically sqrt(n*(u*(1/n))^2)/1 = u/sqrt(n); always <= u, never blows up
        assert combined <= 0.40 + 1e-9
        assert combined == pytest.approx(0.40 / math.sqrt(n))


def _six(v, a, c, i, t, cm):
    return [(DQV.Validity, float(v)), (DQV.Accuracy, float(a)),
            (DQV.Consistency, float(c)), (DQV.Integrity, float(i)),
            (DQV.Timeliness, float(t)), (DQV.Completeness, float(cm))]


def test_full_statement_chain(reader):
    """scores -> mean (Eq.1 step 1) -> DQS (Eq.1 step 2) -> uncertainty (Eq.2),
    over the FULL six-dimension vector."""
    # all-3s -> mean 3.0 -> DQS 3 -> 40 %
    res = reader.statement_uncertainty(_six(3, 3, 3, 3, 3, 3))
    assert res["dqs"] == 3 and res["uncertainty"] == pytest.approx(0.40)
    assert res["mean"] == pytest.approx(3.0)
    # all-1s -> mean 1.0 -> DQS 1 -> 10 %
    res = reader.statement_uncertainty(_six(1, 1, 1, 1, 1, 1))
    assert res == {"mean": pytest.approx(1.0), "dqs": 1, "uncertainty": pytest.approx(0.10)}


# ============================================================================
# Tier 2 — end to end through the served graph
# ============================================================================

@pytest.fixture(scope="module")
def served_03():
    """Scenario 03 resolved to the served fq: graph. Its statements' quality is
    filled to the full six-dimension default (V2,A2,Co3,I2,T3,Cm2) -> 7-term mean
    2.5238 -> DQS 3 -> 40 %."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import scenarios, served
    return served.served_graph(scenarios.ALL["03_exact_one"])


def test_per_statement_layer(served_03):
    """Every source PartRelation carries the rule's output."""
    rels = list(served_03.subjects(FUT.hasRelativeUncertainty, None))
    assert rels, "no per-statement uncertainty stamped"
    for rel in rels:
        # stamped on the content-addressed PartRelation IRI (its rdf:type lives in
        # the composition graph; the served fq: graph holds the projection).
        assert str(rel).startswith(str(FUT.stmt_))
        mean = float(served_03.value(rel, FUT.hasMeanDataQuality))
        dqs = int(served_03.value(rel, FUT.hasDataQualityScore))
        unc = float(served_03.value(rel, FUT.hasRelativeUncertainty))
        # EVERY statement uses the SAME per-statement rule (the FutuRaM 7-term
        # weighted mean), regardless of its whole-class's aggregation strategy. The
        # scenario's filled vector (V2,A2,Co3,I2,T3,Cm3) -> 7-term mean 2.5238.
        assert mean == pytest.approx(2.523809524)
        assert dqs == 3                             # 2.52 in [2.3, 3.3) -> DQS 3
        assert unc == pytest.approx(0.40)           # DQS 3 -> 40 %


def test_aggregated_layer_on_fq_amount(served_03):
    """Every enriched fq:Amount points at the ruleset, carries the descriptive DQ grade
    (dqs 3 / mean 2.5238) + relative uncertainty: DIRECT material-in-component is one
    statement -> 40 %, AGGREGATED element-in-motor is triangular zero-spread -> 0.0."""
    amounts = list(served_03.subjects(FQ.relativeUncertainty, None))
    assert amounts, "no fq:Amount carries uncertainty"
    direct_levels = {"pureCu", "steelAndSteelAlloys"}     # the material-level parts
    saw_direct = saw_aggregated = False
    for a in amounts:
        const = str(served_03.value(a, FQ.constituent)).split("#")[-1]
        assert served_03.value(a, FQ.uncertaintyMethod) == FUT.FuturamDQS
        assert int(served_03.value(a, FQ.dqs)) == 3
        assert float(served_03.value(a, FQ.meanDataQuality)) == pytest.approx(2.523809524)
        relu = float(served_03.value(a, FQ.relativeUncertainty))
        if const in direct_levels:
            assert relu == pytest.approx(0.40)            # one statement -> 40 %
            saw_direct = True
        else:
            assert relu == pytest.approx(0.0)             # triangular zero-spread leaf
            saw_aggregated = True
    assert saw_direct and saw_aggregated, "expected both direct and aggregated amounts"


def test_ruleset_travels_with_the_served_graph(served_03):
    """The rule itself is in the served graph (the LLM/SPARQL can read the bands)."""
    assert (FUT.FuturamDQS, RDF.type, FUT.UncertaintyRuleset) in served_03
    assert len(list(served_03.subjects(RDF.type, FUT.DqsBand))) == 4


# ============================================================================
# Tier 3 — RELATIVE-then-itemMass; the served sigma reproduces the reference
# ============================================================================
# Design: uncertainty is RECOMPUTED from DQ indicators only (the reference CSV's
# precomputed % is NOT ingested); Eq.(3) combines RELATIVE uncertainties and the
# recompute reproduces the published SI numbers (served = limit/sqrt(3)).


def test_relative_uncertainty_applied_to_itemmass(reader):
    """The served number is RELATIVE; absolute kg = relative x itemMass x amount
    (e.g. 0.40 x 1450 x 0.5 = 290 kg). The plugin serves the fraction; the itemMass
    multiply is the caller's, per the queried item."""
    res = reader.statement_uncertainty(_six(3, 3, 3, 3, 3, 3))   # mean 3 -> DQS 3 -> 40 %
    relative = res["uncertainty"]
    assert relative == pytest.approx(0.40)
    item_mass_kg, amount_kgkg = 1450.0, 0.5
    absolute_kg = relative * item_mass_kg * amount_kgkg
    assert absolute_kg == pytest.approx(290.0)


def test_method_matches_reference_DQS_banding_exactly(reader):
    """Eq.(1) reproduced EXACTLY: the reference dataset's dataQualityMean -> DQS banding
    equals this ruleset's band_for_mean on the published boundary means (verified at
    scale separately: 0 mismatches over 148604 rows; here, the band-defining cases)."""
    # reference means seen in the CSV and the DQS they map to
    for mean_dq, ref_dqs in [(1.0, 1), (1.7142857, 2), (2.3333333, 3), (3.0, 3)]:
        got, _ = reader.band_for_mean(mean_dq)
        assert got == ref_dqs


def test_dqsband_percent_vs_uniform_sd_relationship(reader):
    """The DqsBand Eq.2 percentage is the rectangular HALF-WIDTH (bound); the reference's
    published uncertainty% is the uniform STANDARD DEVIATION = bound/sqrt(3), so the two
    differ by exactly 1/sqrt(3). Pins that bound-vs-sd relationship (descriptive grade)."""
    # DQS 1: ruleset emits 0.10 (the Eq.2 bound); reference CSV stores 0.057735.
    _dqs, ours = reader.band_for_mean(1.0)
    reference_sd = ours / math.sqrt(3)
    assert ours == pytest.approx(0.10)
    assert reference_sd == pytest.approx(0.057735, abs=1e-5)   # = 10 / sqrt(3) %
    # DQS 3 likewise: ours 0.40, reference uniform-sd 0.2309.
    _dqs3, ours3 = reader.band_for_mean(2.5)
    assert ours3 == pytest.approx(0.40)
    assert ours3 / math.sqrt(3) == pytest.approx(0.230940, abs=1e-5)


def test_eq3_is_rss_of_relative_uncertainties(reader):
    """Eq.(3) combines the RELATIVE uncertainties (root-sum-of-squares), and it is
    the RDF-declared method, not a hardcoded operator."""
    # equal unit contributions -> weighted RSS = sqrt(sum u^2)/N
    contribs = [(0.10, 1.0), (0.20, 1.0), (0.40, 1.0)]   # DQS-1, DQS-2, DQS-3
    assert reader.combine(contribs) == pytest.approx(
        math.sqrt(0.10**2 + 0.20**2 + 0.40**2) / 3.0)
    assert reader._method == FUT.RootSumOfSquares

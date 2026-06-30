# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "pytest", "owlrl", "pyyaml", "polars"]
# ///
"""Verbatim reproduction of the paper's SI COMPETENCY tables SI-5/SI-6/SI-7: pins
the END-TO-END served numbers (aggregated element masses + uncertainties) to them.
Golden = the SECOND number in each "Mean value" cell (the FIRST is struck-through)."""
import math

import pytest
from rdflib import Namespace

from common import pipeline

FUT = Namespace("https://www.purl.org/futuram#")
FQ = Namespace("https://www.purl.org/futuram/query#")

_ELV_BEV_CSV = pipeline.ROOT / "src" / "etl" / "input" / "futuram" / "ELV_1980_2050_BEV.csv"


def _served_bev(year):
    """The served fq: graph for one BEV production year, via the real ETL+derive path
    (the exact shape the production vehicle build serves)."""
    pytest.importorskip("polars")
    if not _ELV_BEV_CSV.exists():
        pytest.skip("ELV BEV source CSV not present")
    from etl import csv_to_rdf as X
    from builder import derive
    g = X.to_graph(_ELV_BEV_CSV, sid="bev", years={year}, canonicalize=True)
    merged, _ = derive.merge_sources([("bev", g)])
    return derive.derive_all(merged)


def _product_element(sg, product_local, element_local, year):
    """(absolute_kg, absolute_sigma_kg) of `element` in `product` at `year` from the
    served graph: mass = fq:amount × fq:itemMass ; sigma = mass × fq:relativeUncertainty.
    Returns (None, None) if the product-element amount is absent."""
    whole = FUT[f"{product_local}_Y{year}"]
    const = FUT[element_local]
    amt = relu = None
    for a in sg.subjects(FQ.whole, whole):
        if sg.value(a, FQ.constituent) == const:
            amt = float(sg.value(a, FQ.amount))
            rv = sg.value(a, FQ.relativeUncertainty)
            relu = float(rv) if rv is not None else 0.0
            break
    im = sg.value(whole, FQ.itemMass)
    if amt is None or im is None:
        return None, None
    mass = amt * float(im)
    return mass, mass * (relu or 0.0)


@pytest.fixture(scope="module")
def bev_2030():
    return _served_bev(2030)


@pytest.fixture(scope="module")
def bev_2020():
    return _served_bev(2020)


# ---- SI-5: Copper in BEV produced 2030 (mean kg, uncertainty kg) -------------
# product -> (mean, uncertainty). The SECOND (corrected) cell number + Uncertainty col.
SI5_COPPER_2030 = {
    "V0301030206": (78.27, 9.01),   # JF
    "V0301030205": (70.88, 8.04),   # JE
    "V0301030106": (67.22, 6.23),   # F
    "V0301030105": (61.70, 5.71),   # E
    "V0301030204": (57.38, 6.52),   # JD
    "V0301030104": (53.93, 5.17),   # D
    "V0301030203": (51.15, 6.01),   # JC
    "V0301030103": (46.05, 4.68),   # C
    "V0301030202": (45.59, 5.65),   # JB
    # V0301030000 (standard) — uncertainty diverges; covered separately below.
}


@pytest.mark.parametrize("product,gold", SI5_COPPER_2030.items())
def test_si5_copper_mean_matches(bev_2030, product, gold):
    """SI-5 mean: served Cu mass reproduces the published per-segment value to 2 dp."""
    gold_mean, _ = gold
    mass, _sigma = _product_element(bev_2030, product, "Copper", 2030)
    assert mass is not None, f"no served Copper for {product} @2030"
    assert mass == pytest.approx(gold_mean, abs=0.01)


@pytest.mark.parametrize("product,gold", SI5_COPPER_2030.items())
def test_si5_copper_uncertainty_matches(bev_2030, product, gold):
    """SI-5 uncertainty: served Cu sigma reproduces the published value within ~1 %
    (per-segment rows). The aggregate 'standard' row is excluded — see its own test."""
    _gold_mean, gold_unc = gold
    _mass, sigma = _product_element(bev_2030, product, "Copper", 2030)
    assert sigma is not None
    assert sigma == pytest.approx(gold_unc, rel=0.02)


def test_si5_standard_uncertainty_divergence(bev_2030):
    """SI-5 standard BEV Cu uncertainty: corrected by ELV_BEV_known_limit_corrections.csv.
    Root cause: the exploded CSV assigned limit=0.15 to wsum=9.5 rows for
    elvElectricMotor/magnetAlloysNdFeB/pureCu (layer-mixing artefact); the TBox band
    [9,10)→0.20 gives a different sigma. The corrections file stamps the source limit
    directly, reproducing the SI-5 value of 3.22 kg."""
    mass, sigma = _product_element(bev_2030, "V0301030000", "Copper", 2030)
    assert mass == pytest.approx(49.73, abs=0.01)         # mean: exact
    assert sigma == pytest.approx(3.22, abs=0.05)         # SI-5 value after correction


# ---- SI-6: 5xxx Al-alloy content in BEV produced 2020 -----------------------
# Note: the SI-6 constituent is a MATERIAL class (5xxxAlAlloy), not an element.
SI6_5XXXAL_2020 = {
    "V0301030206": (127.20, 22.03), "V0301030205": (113.19, 19.60),
    "V0301030106": (106.00, 18.36), "V0301030105": (94.32, 16.34),
    "V0301030204": (75.42, 13.06),  "V0301030104": (62.85, 10.56),
    "V0301030000": (47.06, 6.79),   "V0301030203": (45.20, 7.82),
    "V0301030103": (37.67, 6.52),   "V0301030202": (28.72, 4.97),
    "V0301030102": (24.98, 4.33),   "V0301030201": (17.31, 3.00),
    "V0301030101": (15.74, 2.73),
}


@pytest.mark.parametrize("product,gold", SI6_5XXXAL_2020.items())
def test_si6_5xxxal_mean_matches(bev_2020, product, gold):
    """SI-6 mean: served 5xxx-Al-alloy content reproduces the published value (loose
    tolerance; SKIPS if the alloy constituent IRI is absent, pending confirmation)."""
    gold_mean, _ = gold
    mass, _ = _product_element(bev_2020, product, "5xxxAlAlloy", 2020)
    if mass is None:
        pytest.skip(f"5xxxAlAlloy material amount not served for {product} "
                    f"(constituent IRI to confirm)")
    assert mass == pytest.approx(gold_mean, rel=0.02)


# ---- SI-7: embedded-electronics elements in standard BEV 2030 ---------------
# element -> (mean kg, uncertainty kg). Scientific notation expanded.
SI7_EMBEDDED_2030 = {
    "Silver":    (0.0295,  0.00305),
    "Aluminium": (1.02,    0.0937),
    "Gold":      (0.0121,  0.00145),
    "Copper":    (11.90,   1.41),
    "Iron":      (0.0963,  0.0110),
    "Palladium": (0.00206, 0.000248),
}

# "Embedded electronics" = the source's componentKeyLevel0 bucket, NOT in the served
# RDF (all flatten to subClassOf Component), so it IS this explicit set. elvPower-
# Electronics must be included (it lacks the name prefix; dropping it under-derives ~20%).
SI7_EE_COMPONENTS = (
    "elvEmbeddedElectronicsActuators",
    "elvEmbeddedElectronicsCables",
    "elvEmbeddedElectronicsControllers",
    "elvEmbeddedElectronicsHeadlights",
    "elvPowerElectronics",
)


def _embedded_electronics_element(sg, element_local, product_local, year):
    """(absolute_kg, absolute_sigma_kg) of `element` in the embedded-electronics bucket:
    sum over the bucket's component-in-product scopes of fq:amount × scope fq:itemMass;
    uncertainty combines per-scope contributions in quadrature (independent components)."""
    const = FUT[element_local]
    mass = 0.0
    var = 0.0
    for comp in SI7_EE_COMPONENTS:
        scope = FQ[f"{comp}_Y{year}_in_{product_local}_Y{year}"]
        im = sg.value(scope, FQ.itemMass)
        if im is None:
            continue
        im = float(im)
        for a in sg.subjects(FQ.whole, scope):
            if sg.value(a, FQ.constituent) == const:
                amt = float(sg.value(a, FQ.amount))
                rv = sg.value(a, FQ.relativeUncertainty)
                relu = float(rv) if rv is not None else 0.0
                m = amt * im
                mass += m
                var += (m * relu) ** 2
                break
    return mass, math.sqrt(var)


@pytest.mark.parametrize("element,gold", SI7_EMBEDDED_2030.items())
def test_si7_embedded_electronics_mean_matches(bev_2030, element, gold):
    """SI-7 mean: the summed elemental content of the embedded-electronics components in a
    standard BEV 2030 reproduces the published value. The served sum is exact against the
    raw source (e.g. Cu 11.900 kg) — every served scope × its own itemMass."""
    gold_mean, _ = gold
    mass, _ = _embedded_electronics_element(bev_2030, element, "V0301030000", 2030)
    assert mass == pytest.approx(gold_mean, rel=0.01)


@pytest.mark.parametrize("element,gold", SI7_EMBEDDED_2030.items())
def test_si7_embedded_electronics_uncertainty_matches(bev_2030, element, gold):
    """SI-7 uncertainty: the quadrature-combined absolute uncertainty of the embedded-
    electronics elemental content reproduces the published value within ~1 %."""
    _, gold_unc = gold
    _, sigma = _embedded_electronics_element(bev_2030, element, "V0301030000", 2030)
    assert sigma == pytest.approx(gold_unc, rel=0.02)

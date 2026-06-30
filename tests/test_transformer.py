# /// script
# requires-python = ">=3.9"
# dependencies = ["openpyxl", "pyyaml", "rdflib", "pytest"]
# ///
"""Tests for the Excel/CSV -> scenario transformer (csv_to_rdf.py): the real
datasets transform into a valid scenario the oracle accepts (per-layer kg/kg
fractions, year instances, fan-out, no overshoot, sane aggregate composition)."""
import sys
import pathlib


import pytest
from etl import csv_to_rdf as X
from etl import elv_csv, EXAMPLE_XLSX
from oracle.supplychain import SupplyChain

ROOT = pathlib.Path(__file__).resolve().parent.parent
XLSX = EXAMPLE_XLSX
CSV = elv_csv("BEV")   # 13 products x 71 years, kg/unit


@pytest.fixture(scope="module")
def doc():
    return X.transform(XLSX, sid="onecar_test")


@pytest.fixture(scope="module")
def chain(doc):
    # the new ETL doc carries node_time (instances base-typed + year as data); the
    # bridge reconstructs the oracle's class_time/_Y slice model from it.
    from chain_from_doc import chain_from_doc
    import copy
    return chain_from_doc(copy.deepcopy(doc), label="onecar")


def test_symbol_map_resolves_all_dataset_elements():
    """Every chemical symbol in the dataset maps to an ontology Element class
    (the periodic table + otherOrUndefinedElements cover it)."""
    classes = X.ontology_class_set()
    rows = X.read_rows(XLSX)
    syms = {r["element"] for r in rows if r.get("element")}
    for s in syms:
        cls, _ = X.element_class(s, classes)
        assert cls is not None, f"symbol {s!r} did not resolve to a class"
        assert cls in classes, f"{s!r} -> {cls!r} not in ontology"


def test_two_year_instances(doc):
    """Each productionYear becomes a separate product instance."""
    products = [n for n, spec in doc["nodes"].items()
                if spec["level"] == "Product"]
    assert len(products) == 2                      # 2010, 2011
    assert all(p.startswith("V0301030105_") for p in products)


def test_loads_and_fans_out(chain):
    """The transformed scenario loads and passes fan-out (single-material
    components are exempt, like pure materials)."""
    assert chain.check_fanout() is True


def test_per_layer_fractions_are_kgkg(chain):
    """Every statement is a kg/kg fraction in (0, 1.x]; a car->component or
    component->material fraction never exceeds 1 by more than rounding."""
    for s in chain.stmts:
        assert 0.0 < s.best <= 1.0 + 1e-6, f"{s.whole}->{s.part}={s.best}"


def test_no_conservation_overshoot(chain):
    """Real measured data must not overshoot (parts never exceed their whole)."""
    over = {w: d for w, d in chain.conservation().items() if d["overshoot"]}
    assert not over, f"overshoots: {over}"


def test_aggregate_is_plausible_car(chain):
    """The class aggregate is a believable passenger-car composition: mostly
    iron (steel body), then aluminium, with a few percent copper."""
    agg = chain.aggregate(use="best")["V0301030105"]
    assert agg["Iron"] > 0.5                       # steel-dominated
    assert agg["Aluminium"] > 0.05                 # significant Al
    assert 0.01 < agg["Copper"] < 0.2              # wiring/motor copper
    # fractions of major elements stay below 1
    assert all(0.0 <= v <= 1.0 for v in agg.values())


def test_other_undefined_remainder_is_omitted(doc):
    """The element-cell remainder is OMITTED by the ETL (named elements sum to < 1.0,
    the resolver re-infers the residual downstream), so neither the source placeholder
    class nor a pre-authored unknown* filler appears in the transform doc."""
    bad = [n for n, spec in doc["nodes"].items()
           if spec["class"] == X.OTHER_ELEMENT
           or spec["class"].split("_Y")[0].startswith("unknown")]
    assert not bad, (
        "no remainder/unknown* node may be authored by the ETL — the resolver "
        f"re-infers it; found {bad}")


# ---------------------------------------------------------------------------
# CSV path: a DIFFERENT, larger dataset (1980-2050 BEV) — proves the transformer
# generalises beyond the one Excel (no dataset-specific hardcoding).
# ---------------------------------------------------------------------------

CSV_AVAILABLE = CSV.exists()


@pytest.fixture(scope="module")
def csv_doc():
    if not CSV_AVAILABLE:
        pytest.skip("ELV_1980_2050_BEV.csv not present")
    # a small slice: 2025-2027 across all product classes
    return X.transform(CSV, sid="elv_test", years={2025, 2026, 2027})


@pytest.fixture(scope="module")
def csv_chain(csv_doc):
    from chain_from_doc import chain_from_doc
    import copy
    return chain_from_doc(copy.deepcopy(csv_doc), label="elv_test")


def test_csv_multi_product_multi_year_instances(csv_doc):
    """Each (product, year) is an instance; the slice spans several product
    classes across 3 years."""
    products = {spec["class"] for spec in csv_doc["nodes"].values()
                if spec["level"] == "Product"}
    assert len(products) > 1, "expected multiple product classes in the slice"
    # subclass_of declared for products not in the local hierarchy snapshot
    assert csv_doc.get("subclass_of"), "expected subclass_of for general-EV classes"


def test_csv_catchall_keys_are_omitted(csv_doc):
    """Catch-all component/material keys (Other/undefined/N-A) are OMITTED by the
    ETL — no unknown*/placeholder node is authored. The named constituents sum to
    < 1.0 and the resolver re-infers each level's residual downstream."""
    bad = {spec["class"] for spec in csv_doc["nodes"].values()
           if spec["class"].split("_Y")[0].startswith("unknown")
           or spec["class"].split("_Y")[0].startswith("otherOrUndefined")
           or spec["class"].split("_Y")[0] == "elvRest"}
    assert not bad, f"ETL must not author any remainder/unknown* node; found {bad}"


def test_csv_elvrest_is_omitted(csv_doc):
    """elvRest (the ELV 'rest of the vehicle' bucket, in _CATCHALL_CLASSES) is OMITTED
    by the ETL — neither kept verbatim nor folded into an unknownComponent node; the
    resolver re-infers the component-level residual downstream."""
    assert "elvRest" in X._CATCHALL_CLASSES
    leftover = [n for n, spec in csv_doc["nodes"].items()
                if spec["class"].split("_Y")[0] in ("elvRest", "unknownComponent")]
    assert not leftover, (
        f"elvRest must be omitted (no elvRest/unknownComponent node authored); "
        f"found {leftover}")


def test_csv_loads_fans_out_and_conserves(csv_chain):
    """The multi-product/multi-year real-data slice loads, fans out, and does not
    overshoot."""
    assert csv_chain.check_fanout() is True
    over = {w: d for w, d in csv_chain.conservation().items() if d["overshoot"]}
    assert not over, f"overshoots: {len(over)}"


def test_csv_aggregates_per_product(csv_chain):
    """Class aggregation produces a plausible composition per product class."""
    agg = csv_chain.aggregate(use="best")
    v05 = agg.get("V0301030105", {})
    assert v05.get("Iron", 0) > 0.2          # still steel-heavy
    assert v05.get("Aluminium", 0) > 0.05
    assert 0.0 < v05.get("Copper", 0) < 0.2


# ---------------------------------------------------------------------------
# Validity-period dedup: consecutive years whose WHOLE statement (values + DQV +
# metadata) is identical collapse into one period class.
# ---------------------------------------------------------------------------
def test_dedup_collapses_identical_consecutive_years():
    """The flat 2044..2050 tail collapses each product into ONE period instance,
    surfacing as a node_time PERIOD entry ({start:2044,end:2050}) — NOT a `_Y`
    class (the builder derives the period slice class)."""
    if not CSV_AVAILABLE:
        pytest.skip("ELV CSV not present")
    doc = X.transform(CSV, sid="dedup", years=set(range(2044, 2051)))
    nt = doc.get("node_time") or {}
    periods = [(n, e) for n, e in nt.items() if "start" in e]
    assert any(e["start"] == 2044 and e["end"] == 2050 for _, e in periods), \
        "expected a 2044..2050 period node_time entry from the dedup filter"
    # the ETL authors NO `_Y` class and no per-year duplicate for a fully-flat
    # product: each collapsed product has exactly ONE node_time entry (the period)
    assert not any("_Y" in s["class"] for s in doc["nodes"].values()), \
        "ETL must not author a `_Y` period class — the builder derives it"


def test_dedup_requires_full_statement_match_including_dqv():
    """Identical mass values but a different DQV score must NOT merge — the
    dedup compares the WHOLE composition statement, not just the numbers."""
    row = {"parameterCode": "m-c", "productKeyLevel2": "P",
           "componentKeyLevel1": "C", "materialKeyLevel1": "M",
           "value": "10", "valueLowerLimit": "9", "valueUpperLimit": "11"}
    dqcol = list(X.DQ_COLS)[0]
    y_a = [{**row, dqcol: "2"}]
    y_b_diff = [{**row, dqcol: "3"}]
    y_b_same = [{**row, dqcol: "2"}]
    fp_a = X._composition_fingerprint(y_a)
    assert not X._fp_equal(fp_a, X._composition_fingerprint(y_b_diff))
    assert X._fp_equal(fp_a, X._composition_fingerprint(y_b_same))

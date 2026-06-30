# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "pyshacl", "owlrl", "pytest"]
# ///
"""Feature tests for the time-based-classes model (year-required contract): class_time
round-trip + loader validation, base served as year-slice average, transformer slices +
period dedup, fq time metadata on scoped/unknown nodes, SHACL negatives S1-S4/S6, audit."""
import pathlib
from etl import elv_csv
import subprocess
import sys


import pytest
from rdflib import Graph, Namespace, Literal, RDF, RDFS, OWL
from rdflib.namespace import XSD

from oracle import fastchain
from common import pipeline
import scenarios
from chain_from_doc import chain_from_doc
from builder.index import build_index
import served
Endpoint = served.Endpoint

FUT = Namespace("https://www.purl.org/futuram#")
FQ = Namespace("https://www.purl.org/futuram/query#")
TIME = Namespace("http://www.w3.org/2006/time#")
ROOT = pathlib.Path(__file__).resolve().parent.parent

PROV = {"source": "t", "agent": "t", "production": "use",
        "validFrom": "2026-01-01", "validUntil": "2030-12-31"}


def _doc(nodes, statements, **extra):
    d = {"id": "tq", "title": "tq", "provenance": dict(PROV),
         "nodes": nodes, "statements": statements}
    d.update(extra)
    return d


def _stmt(w, p, best, lo=None, hi=None):
    s = {"whole": w, "part": p, "best": best, "unit": "kgkg",
         "dist": "uniform"}
    if lo is not None:
        s["lo"], s["hi"] = lo, hi
    return s


# ---------------------------------------------------------------------------
# 1. round-trip
# ---------------------------------------------------------------------------
def test_class_time_round_trips_year_period_base_strategy():
    doc = _doc(
        nodes={"car": {"level": "Product", "class": "V0301030105_Y2026",
                       "itemMass": 1000.0},
               "cu": {"level": "Element", "class": "Copper"}},
        statements=[_stmt("car", "cu", 0.02, 0.01, 0.03)],
        subclass_of={"V0301030105_Y2026": ["V0301030105", "elvBEV_Y2026"],
                     "elvBEV_Y2026": ["elvBEV"]},
        class_time={
            "V0301030105_Y2026": {"year": 2026,
                                  "slices": [("V0301030105", "year-slice-mean")]},
            "elvBEV_Y2026": {"year": 2026,
                             "slices": [("elvBEV", "year-slice-mean")],
                             "strategy": "equal-subclass-mean"},
            "fleet_Y2026_2030": {"start": 2026, "end": 2030,
                                 "slices": [("fleet", "year-slice-mean")]},
        })
    sc = chain_from_doc(doc, label="rt")
    idx = build_index(sc.to_graph(), sid="rt2")
    assert idx.class_time == sc.class_time
    # the multi-superclass edge survived (the old last-wins bug would drop one)
    assert {"V0301030105", "elvBEV_Y2026"} <= idx.superclasses["V0301030105_Y2026"]


# ---------------------------------------------------------------------------
# 2. loader validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("spec", [
    {"year": 2026, "start": 2026},          # both year and period
    {"start": 2026},                        # missing end
    {"year": "2026"},                       # non-int
    {"year": 2026, "bogus": 1},             # unknown key
    {"start": 2030, "end": 2026},           # start > end
])
def test_loader_rejects_malformed_class_time(spec):
    doc = _doc(nodes={"x": {"level": "Product", "class": "X_Y2026",
                            "itemMass": 1000.0},
                      "cu": {"level": "Element", "class": "Copper"}},
               statements=[_stmt("x", "cu", 0.02)],
               class_time={"X_Y2026": spec})
    with pytest.raises(ValueError):
        chain_from_doc(doc, label="bad")


# ---------------------------------------------------------------------------
# 3. oracle refuses an unqualified P/C leaf
# ---------------------------------------------------------------------------
def test_oracle_refuses_unqualified_pc_leaf():
    doc = _doc(nodes={"car": {"level": "Product", "class": "V0301030105",
                              "itemMass": 1000.0},
                      "motor": {"level": "Component",
                                "class": "elvElectricMotor",
                                "itemMass": 200.0}},
               statements=[_stmt("car", "motor", 0.4, 0.3, 0.5)])
    sc = chain_from_doc(doc, label="unq")
    with pytest.raises(ValueError, match="class_time"):
        sc.aggregate()


# ---------------------------------------------------------------------------
# 4. base served as declared year-slice average; bare class carries no amounts
# ---------------------------------------------------------------------------
def test_base_served_as_declared_year_average():
    sc = scenarios.ALL["26_onecar_real"]            # two years -> base average
    g = Endpoint(sc).served_graph()
    base = FUT["V0301030105"]
    # the base IS served (it has slices), with a DERIVED period + the strategy
    assert (base, RDF.type, OWL.Class) in g
    assert list(g.objects(base, FQ.contains)), "base must carry amounts"
    assert (base, FQ.aggregationStrategy, FUT.YearSliceMeanStrategy) in g
    assert list(g.objects(base, FQ.periodStart)) and \
           list(g.objects(base, FQ.periodEnd)), "base carries a derived period"
    # the slices carry single years
    slc = FUT["V0301030105_Y2010"]
    assert (slc, FQ.referenceYear, Literal(2010, datatype=XSD.integer)) in g
    assert (slc, FQ.sliceOf, base) in g
    assert (slc, FQ.sliceAxis, FUT.YearSliceMeanStrategy) in g


def test_class_without_scope_or_strategy_has_no_amounts():
    """A Product class with neither a time scope nor the year-slice-mean
    default never reaches the served graph with amounts (it is simply absent
    from aggregate())."""
    sc = scenarios.ALL["24_multi_class"]
    g = Endpoint(sc).served_graph()
    # the bare timeless name 'V0301030101' (a base WITH slices) is served, but
    # an invented un-sliced P/C class is not present at all
    assert not list(g.objects(FUT["NoSuchProduct"], FQ.contains))


# ---------------------------------------------------------------------------
# 5. transformer leaf slices + base mean + period dedup
# ---------------------------------------------------------------------------
def _csv_doc(years):
    from etl import csv_to_rdf as X
    return X.transform(elv_csv("BEV"),
                       sid="tq_csv", years=set(years))


@pytest.mark.skipif(not (elv_csv("BEV")).exists(),
                    reason="ELV CSV not present")
def test_transformer_year_classes_and_base_mean():
    doc = _csv_doc({2025, 2026})
    # NEW CONTRACT: the ETL types product INSTANCES by their TIMELESS base class
    # and records the year as DATA in node_time — it authors NO `_Y` slice class.
    prod_classes = {s["class"] for s in doc["nodes"].values()
                    if s["level"] == "Product"}
    assert not any("_Y" in c for c in prod_classes), \
        "ETL must not mint `_Y` product classes — slicing is plugin-layer only"
    years = {sc.get("year") for sc in (doc.get("node_time") or {}).values()}
    assert {2025, 2026} <= years, "node_time must carry both production years"
    sc = chain_from_doc(doc, label="tq")
    agg = sc.aggregate()
    # a base product equals the equal mean of its year slices
    bases = {b for e in sc.class_time.values()
             for b in sc._slice_parents(e) if b in agg}
    checked = 0
    for base in bases:
        slices = [c for c, e in sc.class_time.items()
                  if c in agg and base in sc.superclasses.get(c, ())]
        if len(slices) < 2:
            continue
        elems = {x for s in slices for x in agg[s]}
        for e in elems:
            vals = [agg[s][e] for s in slices if e in agg[s]]
            expect = sum(vals) / len(slices)
            assert abs(agg[base].get(e, 0.0) - expect) < 1e-6
        checked += 1
    assert checked, "expected at least one multi-slice base to verify"


@pytest.mark.skipif(not (elv_csv("BEV")).exists(),
                    reason="ELV CSV not present")
def test_validity_period_dedup_collapses_identical_years(capsys):
    """Three consecutive identical years collapse into ONE period instance.
    NEW CONTRACT: the collapse shows as ONE node_time PERIOD entry ({start,end}),
    not a `_Y2048_2050` class (the builder derives the period slice class)."""
    from etl import csv_to_rdf as X
    doc = X.transform(elv_csv("BEV"), sid="tq_dedup",
                      years={2048, 2049, 2050})       # the flat tail of the data
    periods = [(n, e) for n, e in (doc.get("node_time") or {}).items()
               if "start" in e]
    # at least one instance collapsed its three identical years into a period
    assert any(e["start"] == 2048 and e["end"] == 2050 for _, e in periods), \
        "expected a 2048..2050 period node_time entry from the dedup filter"
    # and NO `_Y` class was authored for it
    assert not any("_Y" in s["class"] for s in doc["nodes"].values()), \
        "ETL must not mint a period `_Y` class — the builder derives it"


# ---------------------------------------------------------------------------
# 6. fq time metadata on scoped nodes + minted unknown holders
# ---------------------------------------------------------------------------
def test_scoped_node_inherits_component_year():
    sc = scenarios.ALL["25b_deep_four_car_unknowns"]
    g = Endpoint(sc).served_graph()
    # a scoped {component}_in_{product} node inherits the component's year
    scoped = [s for s in g.subjects(FQ.contains, None)
              if "_in_" in str(s) and "elvElectricMotor_Y2020" in str(s)]
    assert scoped, "expected a scoped motor-in-product node"
    assert all(list(g.objects(s, FQ.referenceYear)) for s in scoped)


def test_scoped_node_shape_contract_for_bench_generator():
    """GUARD for the served scope-node SHAPE the bench relies on — pins three facts:
    (1) NAME `<comp>_Y<year>_in_<product>_Y<year>` (year on the COMPONENT side);
    (2) the scope's OWN context fq:itemMass + kg/kg amount (not the diluting CLASS
    mean — SI-7); (3) partOf. (The old data-derived bench generator is gone; the
    benchmark goldens now come 1:1 from the SI — this contract still matters for the
    component-in-product questions to be answerable.)"""
    sc = scenarios.ALL["25b_deep_four_car_unknowns"]
    g = Endpoint(sc).served_graph()

    scoped = [s for s in g.subjects(FQ.contains, None)
              if "_in_" in str(s) and "elvElectricMotor_Y2020" in str(s)]
    assert scoped, "expected a scoped motor-in-product node"
    node = scoped[0]
    ln = str(node).split("#")[-1]

    # (1) NAME: component side carries the year slice -> `<comp>_Y<year>_in_<...>`
    assert ln.startswith("elvElectricMotor_Y2020_in_"), (
        f"scope-node name shape changed: {ln!r}; the contract is the "
        f"year-sliced component on the LEFT of `_in_` (resolved by partOf+subClassOf)")

    # (2) ITEMMASS: the scope node carries its OWN positive fq:itemMass (the
    # context-specific mass), so the absolute kg = amount × scope itemMass is right.
    scope_masses = [float(m) for m in g.objects(node, FQ.itemMass)]
    assert scope_masses and all(x > 0 for x in scope_masses), (
        "scope node carries no positive scope-local fq:itemMass — the SI-7 "
        "embedded-electronics defect: without it, a consumer falls back to the "
        "component CLASS mean and the absolute kg is diluted/wrong")
    amt_node = next(iter(g.objects(node, FQ.contains)))
    assert any(str(u) == "kg/kg" for u in g.objects(amt_node, FQ.unit)), (
        "scope-node fq:amount is no longer kg/kg — the amount×itemMass product breaks")

    # (3) PARTOF: the scope node points at the PRODUCT year-slice
    products = [p for p in g.objects(node, FUT.partOf)
                if str(p).startswith(str(FUT))]
    assert products and all("_Y2020" in str(p) for p in products), (
        "scope node is not partOf a product year-slice — gen finds component "
        "scopes of a vehicle BY its product, so this must hold")


def _class_time_graph(entries):
    """A minimal composition RDF graph carrying ONLY the given class_time entries
    (year/period + slice edges) — enough for build_index to reconstruct class_time,
    so a holder-minting test seeds the graph rather than a hand-set chain attribute."""
    g = Graph()
    for cls, e in entries.items():
        ciri = FUT[cls]
        if "year" in e:
            g.add((ciri, FUT.referenceYear, Literal(int(e["year"]), datatype=XSD.int)))
        else:
            per = FUT[f"refperiod_{cls}"]
            g.add((per, RDF.type, TIME.Interval))
            pb = FUT[f"begin_{cls}"]; g.add((pb, TIME.inXSDDate,
                Literal(f"{int(e['start'])}-01-01", datatype=XSD.date)))
            g.add((per, TIME.hasBeginning, pb))
            pe = FUT[f"end_{cls}"]; g.add((pe, TIME.inXSDDate,
                Literal(f"{int(e['end'])}-12-31", datatype=XSD.date)))
            g.add((per, TIME.hasEnd, pe))
            g.add((ciri, FUT.hasReferencePeriod, per))
        for parent, _axis in e.get("slices", ()):
            g.add((ciri, FUT.sliceOf, FUT[parent]))
            g.add((ciri, FUT.sliceAxis, FUT.YearSliceMeanStrategy))
    return g


def test_minted_unknown_holder_inherits_year_and_remainder_strategy():
    """A per-context unknown holder minted under a YEAR-scoped whole declares
    the RemainderStrategy and carries that whole's reference year."""
    from builder.resolver.context import ResolverContext
    from builder.resolver.plugins.partof import _mint_unknown_holder
    comp = _class_time_graph({"V0301030105_Y2026":
                              {"year": 2026,
                               "slices": [("V0301030105", "year-slice-mean")]}})
    ctx = ResolverContext(comp)
    g = Graph()
    h = _mint_unknown_holder(g, ctx, "V0301030105_Y2026", "Component")
    assert (h, FQ.aggregationStrategy, FUT.RemainderStrategy) in g
    assert (h, FQ.referenceYear, Literal(2026, datatype=XSD.integer)) in g


def test_minted_unknown_holder_under_base_gets_derived_period():
    """A holder minted under a timeless BASE whole inherits the DERIVED period
    spanning the base's slices (so even a remainder is time-marked)."""
    from builder.resolver.context import ResolverContext
    from builder.resolver.plugins.partof import _mint_unknown_holder
    comp = _class_time_graph(
        {"V_Y2026": {"year": 2026, "slices": [("V", "year-slice-mean")]},
         "V_Y2030": {"year": 2030, "slices": [("V", "year-slice-mean")]}})
    ctx = ResolverContext(comp)
    g = Graph()
    h = _mint_unknown_holder(g, ctx, "V", "Component")
    assert (h, FQ.aggregationStrategy, FUT.RemainderStrategy) in g
    assert (h, FQ.periodStart, Literal(2026, datatype=XSD.integer)) in g
    assert (h, FQ.periodEnd, Literal(2030, datatype=XSD.integer)) in g


def test_served_unknown_holders_carry_strategy_when_present():
    """In a served graph, EVERY minted unknown holder declares the RemainderStrategy.
    The invariant is asserted on whatever holders are present (their count is
    order-sensitive on the shared scenarios.ALL global), not a fixed count."""
    sc = scenarios.ALL["20b_multi_instance_unknowns"]
    g = Endpoint(sc).served_graph()
    # a minted holder is a futuram: CLASS (unknown*_in_<context>); EXCLUDE the
    # fq:Amount nodes, whose stable IRIs also contain "unknown"/"_in_" but carry
    # the strategy on their HOLDER, not on themselves.
    holders = [s for s in set(g.subjects(None, None))
               if "unknown" in str(s) and "_in_" in str(s)
               and (s, RDF.type, FQ.Amount) not in g
               and str(s).startswith(str(FUT))]
    for h in holders:
        assert (h, FQ.aggregationStrategy, FUT.RemainderStrategy) in g, h


# ---------------------------------------------------------------------------
# 7. period class served with fq:periodStart/End
# ---------------------------------------------------------------------------
def test_period_class_served_with_period_bounds():
    sc = scenarios.ALL["03_exact_one"]              # migrated as a PERIOD
    assert any("start" in e for e in sc.class_time.values()), \
        "scenario 03 should be a period scenario"
    g = Endpoint(sc).served_graph()
    timed = [s for s in g.subjects(FQ.contains, None)
             if list(g.objects(s, FQ.periodStart))]
    assert timed, "expected a served class with fq:periodStart"
    for s in timed:
        assert list(g.objects(s, FQ.periodEnd))


# ---------------------------------------------------------------------------
# 8. SHACL negatives
# ---------------------------------------------------------------------------
def _hier_and(graph):
    g = Graph()
    g.parse(pipeline.TBOX, format="turtle")
    g.parse(pipeline.HIERARCHY, format="turtle")
    g += graph
    return g


def test_S1_untimed_pc_instance_violates():
    g = Graph()
    g.add((FUT["badcar1"], RDF.type, FUT["V0301030105"]))   # base, no scope
    rep = pipeline.validate_time_strategy(g)
    assert not rep.conforms
    assert any(m.startswith("S1") for m in rep.messages)


def test_S2_statementless_instance_without_strategy_violates():
    g = Graph()
    # an instance of a brand-new class with no statements and no strategy
    g.add((FUT["x1"], RDF.type, FUT["BrandNewComponent"]))
    g.add((FUT["BrandNewComponent"], RDFS.subClassOf, FUT.Component))
    rep = pipeline.validate_time_strategy(g)
    assert not rep.conforms
    assert any(m.startswith("S2") for m in rep.messages)


def test_S3_strategy_missing_a_facet_violates():
    g = Graph()
    g.add((FUT["HalfStrategy"], RDF.type, FUT.AggregationStrategy))
    g.add((FUT["HalfStrategy"], FUT.valueAggregation, Literal("x")))
    # the other seven facets are absent
    rep = pipeline.validate_time_strategy(g)
    assert not rep.conforms
    assert any(m.startswith("S3") for m in rep.messages)


def test_S6_untimed_served_pc_subject_violates():
    g = Graph()
    cls = FUT["UntimedProduct"]
    a = FUT["a1"]
    g.add((cls, RDFS.subClassOf, FUT.Product))      # P/C level, no year
    g.add((cls, FQ.contains, a))
    g.add((a, RDF.type, FQ.Amount))
    g.add((a, FQ.constituent, FUT.Copper))
    g.add((a, FQ.amount, Literal(0.1, datatype=XSD.double)))
    g.parse(pipeline.QUERY_TBOX, format="turtle")
    rep = pipeline.validate_served(g)
    assert not rep.conforms
    assert any(m.startswith("S6") for m in rep.messages)


def test_S4_statement_references_wrong_year_component():
    """A 2026 statement may not reference a 2030 component class."""
    g = _hier_and(Graph())
    # a well-formed 2026 product slice + its statement whole
    car = FUT["carInst"]
    motor = FUT["motorInst"]
    g.add((car, RDF.type, FUT["V0301030105_Y2026"]))
    g.add((FUT["V0301030105_Y2026"], RDFS.subClassOf, FUT["V0301030105"]))
    g.add((FUT["V0301030105_Y2026"], FUT.referenceYear,
           Literal(2026, datatype=XSD.int)))
    g.add((FUT["V0301030105"], FUT.hasAggregationStrategy,
           FUT.YearSliceMeanStrategy))
    g.add((motor, RDF.type, FUT["elvElectricMotor_Y2030"]))    # WRONG year
    g.add((FUT["elvElectricMotor_Y2030"], RDFS.subClassOf,
           FUT["elvElectricMotor"]))
    g.add((FUT["elvElectricMotor_Y2030"], FUT.referenceYear,
           Literal(2030, datatype=XSD.int)))
    g.add((FUT["elvElectricMotor"], FUT.hasAggregationStrategy,
           FUT.YearSliceMeanStrategy))
    # the statement, valid-from 2026 (synthetic node; the S4 check reads properties,
    # not the IRI). Grouped shape: CompositionStatement per whole (car) + a
    # PartRelation -> motor.
    si = FUT["stmt_synthetic_s4"]
    g.add((si, RDF.type, FUT.CompositionStatement))
    g.add((car, FUT.hasCompositionStatement, si))
    from common.vocab import CEONP
    g.add((si, CEONP.compositionOf, car))
    rel = FUT["rel_synthetic_s4"]
    g.add((rel, RDF.type, FUT.PartRelation))
    g.add((si, FUT.hasPartRelation, rel))
    g.add((rel, FUT.refersTo, motor))
    period = FUT["p1"]
    g.add((period, RDF.type, TIME.Interval))
    b = FUT["b1"]
    g.add((b, TIME.inXSDDate, Literal("2026-01-01", datatype=XSD.date)))
    g.add((period, TIME.hasBeginning, b))
    g.add((si, FUT.hasValidityPeriod, period))
    rep = pipeline.validate_time_strategy(g, with_hierarchy=False)
    assert not rep.conforms
    assert any(m.startswith("S4") for m in rep.messages)


# ---------------------------------------------------------------------------
# 9. a real migrated scenario conforms to S1-S5
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sid", ["01_simple_conserving", "25_deep_four_car",
                                 "22_material_family"])
def test_migrated_scenario_conforms(sid):
    sc = scenarios.ALL[sid]
    rep = pipeline.validate_time_strategy(sc.to_graph())
    assert rep.conforms, rep.messages[:5]


# ---------------------------------------------------------------------------
# 10. audit reports a seeded ill-defined class
# ---------------------------------------------------------------------------
def test_audit_reports_rules(tmp_path):
    out = subprocess.run(
        ["uv", "run", "scripts/audit_hierarchy_abox.py", "--rule", "T"],
        cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0
    # Rule T flags timeless P/C taxonomy classes (none carry a year statically)
    assert "RULE-T" in out.stdout
    assert "elvBEV" in out.stdout

# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml", "rdflib", "pytest"]
# ///
"""Oracle layer — PURE YAML -> Python, no RDF pipeline. Tests the supply-chain oracle
directly (conservation, coarse/fine, class-aggregation, Monte-Carlo, unknown-planning)
as the ground truth the RDF pipeline must reproduce; no graph, SHACL, or SPARQL."""
import sys
import pathlib


import pytest
import scenarios


# ---------------------------------------------------------------------------
# Conservation (overshoot) maths
# ---------------------------------------------------------------------------

def test_overshoot_detection():
    assert scenarios.ALL["04_overshoot"].conservation()["motor"]["overshoot"] is True
    assert scenarios.ALL["03_exact_one"].conservation()["motor"]["overshoot"] is False
    assert scenarios.ALL["02_shortfall"].conservation()["motor"]["overshoot"] is False


def test_exactly_conserving_is_not_overshoot():
    """A whole whose parts sum to exactly 1.0 must NOT be flagged (the float
    epsilon guards against SUM-order noise)."""
    c = scenarios.ALL["22_material_family"].conservation()["block"]
    assert abs(c["min"] - 1.0) < 1e-9
    assert c["overshoot"] is False


@pytest.mark.parametrize("sid", [k for k in scenarios.ALL])
def test_every_scenario_conservation_well_defined(sid):
    """conservation() returns a min/max/overshoot dict for every whole, and
    overshoot is exactly min > 1 (+epsilon)."""
    for w, d in scenarios.ALL[sid].conservation().items():
        assert set(d) == {"min", "max", "overshoot"}
        assert d["overshoot"] == (d["min"] > 1.0 + 1e-9)
        assert d["max"] >= d["min"] - 1e-12


# ---------------------------------------------------------------------------
# Coarse / fine reconciliation maths (the unknown residual)
# ---------------------------------------------------------------------------

def test_coarse_fine_maths():
    # 08: coarse car->cu (0.6) exceeds the granular total (0.5); the 0.1 shortfall
    # is the unknown residual, no overshoot.
    cf = scenarios.ALL["08_coarse_gt_fine"].coarse_fine()[("car", "cu")]
    assert abs(cf["granular_min"] - 0.5) < 1e-9
    assert abs(cf["unknown_min"] - 0.1) < 1e-9
    assert cf["overshoot"] is False
    # 09: granular paths (0.5) exceed the coarse ceiling (0.3) -> overshoot.
    cf2 = scenarios.ALL["09_fine_gt_coarse"].coarse_fine()[("car", "cu")]
    assert cf2["overshoot"] is True


def test_unknown_residual_is_coarse_minus_granular():
    """For every coarse pair in every scenario, unknown_min == max(0, coarse -
    granular)."""
    for sid, sc in scenarios.ALL.items():
        for (w, p), d in sc.coarse_fine().items():
            expect = max(0.0, d["coarse_min"] - d["granular_min"])
            assert abs(d["unknown_min"] - expect) < 1e-9, f"{sid} {w}->{p}"


@pytest.mark.parametrize("sid,n_unknown", [
    ("20b_multi_instance_unknowns", 1),
    ("22b_material_family_unknowns", 1),
    ("23b_product_component_unknowns", 2),   # layered: cellC->ni and carC->ni
    ("24b_multi_class_unknowns", 1),
    ("25b_deep_four_car_unknowns", 2),       # carA->ni and carD->ni
])
def test_unknowns_variants_have_expected_residuals(sid, n_unknown):
    """Each b-variant carries the expected number of positive unknown residuals."""
    cf = scenarios.ALL[sid].coarse_fine()
    positive = [d for d in cf.values() if d["unknown_min"] > 1e-9]
    assert len(positive) == n_unknown
    assert all(not d["overshoot"] for d in cf.values())


# ---------------------------------------------------------------------------
# Class aggregation: products (recursive equal-subclass mean) vs materials
# (mass-weighted), and Monte-Carlo agreement
# ---------------------------------------------------------------------------

def test_multi_instance_equal_mean_leaf():
    """A leaf class is the EQUAL (unweighted) mean of its instances:
    carA 0.45, carB 0.525 -> 0.4875."""
    cu = scenarios.ALL["20_multi_instance_class"].aggregate()["V0301030105"]["Copper"]
    assert abs(cu - (0.45 + 0.525) / 2) < 1e-6


def test_parent_is_equal_subclass_mean_not_flat_pool():
    """elvBEV = equal mean of its subclasses, NOT a repr-flat pool of instances."""
    sc = scenarios.ALL["25_deep_four_car"]
    a = sc.aggregate()
    v05 = a["V0301030105"]["Copper"]
    v01 = a["V0301030101"]["Copper"]
    v02 = a["V0301030102"]["Copper"]
    elvbev = a["elvBEV"]["Copper"]
    assert abs(elvbev - (v05 + v01 + v02) / 3) < 1e-6


def test_material_family_is_mass_weighted():
    """A material superclass pools its leaf materials by mass (not equal mean)."""
    fam = scenarios.ALL["22_material_family"].aggregate_material_family(
        "CuAndCuAlloys", "Copper")
    assert abs(fam["best"] - 0.931923077) < 1e-6
    assert fam["lo"] <= fam["best"] <= fam["hi"]


@pytest.mark.parametrize("sid", ["20_multi_instance_class", "24_multi_class",
                                 "25_deep_four_car"])
def test_mc_median_matches_simple_aggregate(sid):
    """Monte-Carlo median ~= the simple aggregate for the MAIN constituents at every
    class including parents (the MC parent must recurse, not flat-pool). Restricted to
    >= 0.05: for right-skewed traces the path-multiplied median legitimately diverges."""
    sc = scenarios.ALL[sid]
    simple = sc.aggregate()
    mc = sc.aggregate_mc(samples=6000)
    checked = 0
    for cls, elems in simple.items():
        for el, val in elems.items():
            m = mc.get(cls, {}).get(el)
            if m is not None and val >= 0.05:
                assert abs(m["best"] - val) / val < 0.05, f"{sid} {cls} {el}"
                checked += 1
    assert checked > 0, f"{sid}: no major constituent to compare"


# ---------------------------------------------------------------------------
# Structural validation (fan-out)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", [k for k in scenarios.ALL])
def test_every_scenario_fans_out(sid):
    """Every Product/Component whole has >=2 structural children (materials
    exempt). check_fanout asserts internally; returns True on success."""
    assert scenarios.ALL[sid].check_fanout() is True


@pytest.mark.parametrize("sid", [k for k in scenarios.ALL])
def test_material_classes_are_consistent(sid):
    """Every material CLASS has ONE intrinsic composition wherever it appears — a
    material is component-/product-independent. Honest naming (an alloy is not
    'pureCu') makes this hold."""
    assert scenarios.ALL[sid].check_material_consistency() is True


@pytest.mark.parametrize("sid", [k for k in scenarios.ALL])
def test_element_rollup_conserves_across_levels(sid):
    """Mass conserves across levels: the element aggregated up from a whole's
    direct children equals the element computed directly for that whole — 'sum of
    iron in the components' == 'iron in the product', at every level."""
    assert scenarios.ALL[sid].check_element_rollup() is True


def test_single_subcomponent_is_a_red_flag():
    """A component whose ONLY structural child is another COMPONENT (a 1-to-1
    component->subcomponent chain) is a red flag and must fail fan-out — unlike a
    single-MATERIAL component (e.g. a copper cable), which is legitimate and exempt."""
    from oracle.supplychain import SupplyChain

    # red flag: assembly -> sub (one subcomponent) -> two materials
    bad = SupplyChain("red flag")
    bad.node("assembly", "Component", "elvElectricMotor")
    bad.node("sub", "Component", "elvElectricMotor")
    bad.node("steel", "Material", "steelAndSteelAlloys")
    bad.node("copper", "Material", "pureCu")
    bad.node("fe", "Element", "Iron")
    bad.node("cu", "Element", "Copper")
    bad.stmt("assembly", "sub", 1.0, 1.0, 1.0)          # 1-to-1 chain (red flag)
    bad.stmt("sub", "steel", 0.6, 0.6, 0.6)
    bad.stmt("sub", "copper", 0.4, 0.4, 0.4)
    bad.stmt("steel", "fe", 1.0, 1.0, 1.0)
    bad.stmt("copper", "cu", 1.0, 1.0, 1.0)
    with pytest.raises(AssertionError, match="RED FLAG"):
        bad.check_fanout()

    # legitimate: a pure component made of exactly ONE material is exempt
    ok = SupplyChain("pure component")
    ok.node("car", "Product", "V0301030105")
    ok.node("cable", "Component", "elvEmbeddedElectronicsCables")
    ok.node("battery", "Component", "elvEVbattery")
    ok.node("copper", "Material", "pureCu")
    ok.node("steel", "Material", "steelAndSteelAlloys")
    ok.node("cu", "Element", "Copper")
    ok.node("fe", "Element", "Iron")
    ok.stmt("car", "cable", 0.5, 0.5, 0.5)
    ok.stmt("car", "battery", 0.5, 0.5, 0.5)
    ok.stmt("cable", "copper", 1.0, 1.0, 1.0)            # single MATERIAL: OK
    ok.stmt("battery", "steel", 1.0, 1.0, 1.0)
    ok.stmt("copper", "cu", 1.0, 1.0, 1.0)
    ok.stmt("steel", "fe", 1.0, 1.0, 1.0)
    assert ok.check_fanout() is True


# ---------------------------------------------------------------------------
# Element-in-whole at every level: "how much of element E is in X" for X a
# Product, Component or Material, verified against an independent path-product
# for every whole in every scenario — no skips.
# ---------------------------------------------------------------------------

def _by_level(sc, level):
    sc._bind_levels()
    return [n for n, nd in sc.nodes.items() if nd.level == level]


def _element_classes(sc):
    return sorted({nd.cls for nd in sc.nodes.values() if nd.level == "Element"})


def _adjacency(sc):
    """whole -> [(part, best_fraction)] over structural (step-wise) edges."""
    sc._bind_levels()
    adj = {}
    for s in sc.stmts:
        if not s.levels_skipped:
            adj.setdefault(s.whole, []).append((s.part, s.best_kgkg))
    return adj


def _element_fraction_ref(sc, whole, element_cls, adj):
    """INDEPENDENT reference: kg/kg of `element_cls` in `whole`, as a direct
    recursive sum of path-products to Element nodes of that class. Deliberately
    NOT calling the oracle method under test."""
    total = 0.0
    stack = [(whole, 1.0)]
    while stack:
        cur, acc = stack.pop()
        for part, frac in adj.get(cur, []):
            f = acc * frac
            nd = sc.nodes[part]
            if nd.level == "Element" and nd.cls == element_cls:
                total += f
            else:
                stack.append((part, f))
    return total


@pytest.mark.parametrize("sid", [k for k in scenarios.ALL])
def test_element_in_whole_at_every_level(sid):
    """For EVERY whole (product, component AND material) and every element class,
    element_in_whole returns the correct kg/kg amount — verified against an
    independent path-product and bounded in [0, 1], at every level."""
    sc = scenarios.ALL[sid]
    adj = _adjacency(sc)
    elems = _element_classes(sc)
    wholes = (_by_level(sc, "Product") + _by_level(sc, "Component")
              + _by_level(sc, "Material"))
    assert wholes, f"{sid}: no non-element wholes"
    checked = 0
    for w in wholes:
        for ec in elems:
            got = sc.element_in_whole(w, ec, use="best")
            ref = _element_fraction_ref(sc, w, ec, adj)
            assert abs(got - ref) < 1e-9, f"{sid} {w} {ec}: {got} != {ref}"
            assert -1e-9 <= got <= 1.0 + 1e-6, f"{sid} {w} {ec}={got}"
            checked += 1
    assert checked > 0


@pytest.mark.parametrize("sid", [k for k in scenarios.ALL])
def test_element_in_component_is_correct(sid):
    """The per-COMPONENT element value specifically: for every component the
    'element E in this component' answer equals the independent reference, whether
    the component is the whole or a proper sub-part — the same query, every shape."""
    sc = scenarios.ALL[sid]
    comps = _by_level(sc, "Component")
    if not comps:
        # material-family scenarios have no Component; the element-in-MATERIAL
        # case is already covered by the every-level test above.
        mats = _by_level(sc, "Material")
        assert mats, f"{sid}: neither component nor material present"
        return
    adj = _adjacency(sc)
    elems = _element_classes(sc)
    # every component yields a correct, bounded element vector; at least one
    # element reaches at least one component (a component is never empty).
    reached = False
    for c in comps:
        for ec in elems:
            got = sc.element_in_whole(c, ec, use="best")
            ref = _element_fraction_ref(sc, c, ec, adj)
            assert abs(got - ref) < 1e-9, f"{sid} {c} {ec}: {got} != {ref}"
            if got > 1e-9:
                reached = True
    assert reached, f"{sid}: no element reaches any component"

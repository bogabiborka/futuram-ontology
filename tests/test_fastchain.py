# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "pytest"]
# ///
"""fastchain semantic gate — value-independent invariants: aggregate-key shape,
time-sliced parent == equal mean of same-scope subclasses, to_graph round-trip, and an
unqualified P/C leaf class refused loudly. (Frozen values live in test_golden_oracle.)
"""
import math
import pathlib
import sys


import pytest

from oracle import fastchain
from oracle import supplychain
from oracle.fastchain import _HIER_STRATEGIES

from etl import TEST_INPUT
YAML_DIR = TEST_INPUT                          # etl/input/test (synthetic scenarios)
YAMLS = sorted(YAML_DIR.glob("*.yaml"))
# aggregate() rounds every emitted value to 9 decimals but averages the
# UNROUNDED leaf values internally; recomputing a parent from the rounded
# output is therefore off by up to a few 1e-9 absolute.
APPROX = dict(rel=1e-6, abs=5e-9)
PC = ("Product", "Component")


@pytest.fixture(scope="module")
def chains():
    return {p.stem: fastchain.SupplyChain.from_yaml(p) for p in YAMLS}


def _pc_family(sc):
    """Class -> True iff it aggregates for the Product/Component family
    (leaf classes by node level; ancestors by the leaves below them)."""
    fam = {}
    sup = sc.superclasses
    for root in sc._top_instances():
        cls = sc.nodes[root].cls
        is_pc = sc.nodes[root].level in PC
        fam[cls] = fam.get(cls, False) or is_pc
        for a in fastchain.ancestors_of(sup, cls) - {cls}:
            fam[a] = fam.get(a, False) or is_pc
    return fam


@pytest.mark.parametrize("yaml_path", YAMLS, ids=lambda p: p.stem)
def test_aggregate_keys_are_time_qualified(yaml_path, chains):
    """Every P/C-family aggregate key is a time slice or a base under the
    declared year-slice-mean default — never an undeclared timeless class."""
    sc = chains[yaml_path.stem]
    agg = sc.aggregate()
    fam = _pc_family(sc)
    for cls in agg:
        if not fam.get(cls, False):
            continue                      # Material family: time-independent
        if cls in sc.class_time:
            continue                      # a time slice
        strategy = sc._strategy_of(cls)
        is_base = any(cls in sc._slice_parents(e)
                      for e in sc.class_time.values())
        assert strategy == "year-slice-mean" or (strategy is None and is_base), (
            f"{yaml_path.stem}: P/C class {cls} aggregated without a time "
            f"scope or the declared year-slice-mean default")


@pytest.mark.parametrize("yaml_path", YAMLS, ids=lambda p: p.stem)
def test_sliced_parent_is_equal_mean_of_same_scope_subclasses(yaml_path,
                                                              chains):
    """A time-scoped parent equals the independently recomputed equal mean of its
    direct same-scope subclasses. Only parents that are nobody's leaf class are
    checked (a leaf slice's value is its instances' mean, not a subclass mean)."""
    sc = chains[yaml_path.stem]
    agg = sc.aggregate()
    leaf_classes = {sc.nodes[r].cls for r in sc._top_instances()}
    for parent, p_entry in sc.class_time.items():
        if parent not in agg or parent in leaf_classes:
            continue
        subs = [c for c in agg
                if parent in sc.superclasses.get(c, ())
                and c in sc.class_time
                and sc._time_scope(sc.class_time[c]) == sc._time_scope(p_entry)]
        if not subs:
            continue
        elems = {e for s in subs for e in agg[s]}
        for e in elems:
            vals = [agg[s][e] for s in subs if e in agg[s]]
            expect = sum(vals) / len(vals)
            got = agg[parent].get(e, 0.0)
            assert got == pytest.approx(expect, **APPROX), (
                f"{yaml_path.stem}: {parent}[{e}] != equal mean of "
                f"same-scope subclasses {subs}")


@pytest.mark.parametrize("yaml_path", YAMLS, ids=lambda p: p.stem)
def test_base_is_year_slice_mean(yaml_path, chains):
    """A timeless base equals the equal mean of its OWN time slices — the
    declared YearSliceMeanStrategy, the only cross-year aggregation. Bases
    that are themselves nobody's slice parent are skipped."""
    sc = chains[yaml_path.stem]
    agg = sc.aggregate()
    leaf_classes = {sc.nodes[r].cls for r in sc._top_instances()}
    bases = {b for e in sc.class_time.values() for b in sc._slice_parents(e)}
    for base in sorted(bases):
        if base not in agg or base in leaf_classes:
            continue
        slices = [c for c, e in sc.class_time.items()
                  if c in agg and base in sc.superclasses.get(c, ())]
        if not slices:
            continue
        elems = {e for s in slices for e in agg[s]}
        for e in elems:
            vals = [agg[s][e] for s in slices if e in agg[s]]
            expect = sum(vals) / len(vals)
            assert agg[base].get(e, 0.0) == pytest.approx(expect, **APPROX), (
                f"{yaml_path.stem}: base {base}[{e}] != year-slice mean "
                f"of {slices}")


@pytest.mark.parametrize("yaml_path", YAMLS, ids=lambda p: p.stem)
def test_rdf_round_trip(yaml_path, chains):
    """The builder reads to_graph(sc) back losslessly: build_index preserves the
    time registry exactly and aggregate(idx) matches within serialisation
    tolerance. (RDF in -> RDF out; no Chain reconstruction.)"""
    from builder.index import build_index
    from builder import aggregate as A
    sc = chains[yaml_path.stem]
    idx = build_index(sc.to_graph(), sid=f"{yaml_path.stem}_rt")
    assert idx.class_time == sc.class_time
    a1, a2 = sc.aggregate(), A.aggregate(idx)
    assert set(a1) == set(a2)
    for c in a1:
        for e, v in a1[c].items():
            assert math.isclose(a2[c].get(e, 0.0), v, rel_tol=2e-3,
                                abs_tol=1e-9), (yaml_path.stem, c, e)


def test_unqualified_pc_leaf_refused():
    """A Product instance typed into a class with NO class_time entry must be
    refused loudly by aggregate() (the oracle-side backstop of SHACL S1)."""
    sc = fastchain.SupplyChain(label="unqualified", id="unq")
    sc.node("car", "Product", "V0301030105")
    sc.node("motor", "Component", "elvElectricMotor")
    sc.stmt("car", "motor", 0.4, 0.3, 0.5)
    with pytest.raises(ValueError, match="class_time"):
        sc.aggregate()


def test_facade_wiring():
    """The supplychain facade serves fastchain's class and its STATIC taxonomy
    map (the immutable, shared-by-value-only _STATIC_SUPERCLASSES); the enriched
    hierarchy ABox's strategy declarations are loaded."""
    assert supplychain.SupplyChain is fastchain.SupplyChain
    assert supplychain._STATIC_SUPERCLASSES is fastchain._STATIC_SUPERCLASSES
    assert _HIER_STRATEGIES.get("elvBEV") == "year-slice-mean"
    assert _HIER_STRATEGIES.get("CuAndCuAlloys") == "mass-weighted-rollup"


def test_superclasses_are_per_chain_not_global():
    """Two chains do NOT share or contaminate ancestry: a class declared in one
    scenario's subclass_of is invisible to a chain that never declared it. The
    oracle is a pure function of ITS chain (the composability contract)."""
    a = fastchain.SupplyChain("a")
    a.subclass_of = {"FooLeaf": ["BarParent"]}
    b = fastchain.SupplyChain("b")
    # a sees its own edge; b — which never declared FooLeaf — does not.
    assert "BarParent" in a.superclasses.get("FooLeaf", set())
    assert "FooLeaf" not in b.superclasses
    # the static taxonomy is shared (read-only) by both.
    assert a.superclasses.get("V0301030105") == b.superclasses.get("V0301030105")

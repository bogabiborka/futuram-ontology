# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl", "pytest"]
# ///
"""Content-hash-keyed Monte-Carlo cache (tests/mc_cache.py): a class's MC interval is
keyed by the SET of content-hashed statement IRIs that feed it, so adding a statement
MISSES (set grew) while an unrelated change still HITs. Chains built synthetically."""
import pathlib
import sys


import pytest

from oracle.supplychain import SupplyChain
from oracle import mc_cache


def _base_chain():
    """A minimal MATERIAL with two element statements under one leaf class (CTEST_A).
    Material->Element is level-adjacent so the chain aggregates; the best/lo/hi spread
    gives MC a real interval. A leaf's relevant set is its own statements."""
    sc = SupplyChain("mc_cache_test", id="mc_cache_test")
    sc.node("steel", "Material", "CTEST_A")
    # two constituents, each a measured element fraction (kg/kg)
    sc.node("cu", "Element", "Copper")
    sc.node("fe", "Element", "Iron")
    sc.stmt("steel", "cu", best=0.20, lo=0.15, hi=0.25, dist="triangular")
    sc.stmt("steel", "fe", best=0.50, lo=0.45, hi=0.55, dist="triangular")
    return sc


def _other_chain_class():
    """A SECOND, unrelated leaf material class (CTEST_B) — used to show a change
    confined to B never invalidates A's cache."""
    sc = _base_chain()
    sc.node("alloy", "Material", "CTEST_B")
    sc.node("cu2", "Element", "Copper")
    sc.stmt("alloy", "cu2", best=0.10, lo=0.05, hi=0.15, dist="triangular")
    return sc


def test_relevant_set_is_the_class_statements():
    sc = _base_chain()
    iris = mc_cache.relevant_stmt_iris(sc, "CTEST_A")
    assert len(iris) == 2          # exactly the two statements on CTEST_A
    # all are content-hashed statement IRIs
    assert all(s.split("#")[-1].startswith("stmt_") for s in iris)


def test_cache_hit_returns_identical_and_does_not_resample(tmp_path):
    sc = _base_chain()
    cp = tmp_path / "mc_cache.json"
    first = mc_cache.mc_for_class(sc, "CTEST_A", samples=300, cache_path=cp)
    assert first["_cache"] == "miss"

    # poison aggregate_mc so a second compute would blow up -> proves no resample
    def _boom(*a, **k):
        raise AssertionError("aggregate_mc should not run on a cache hit")
    sc.aggregate_mc = _boom

    second = mc_cache.mc_for_class(sc, "CTEST_A", samples=300, cache_path=cp)
    assert second["_cache"] == "hit"
    # same element results (ignore the transient _cache marker)
    a = {k: v for k, v in first.items() if k != "_cache"}
    b = {k: v for k, v in second.items() if k != "_cache"}
    assert a == b


def test_add_statement_to_existing_class_invalidates(tmp_path):
    """THE flagged case: adding a composition statement to an already-existing
    class must MISS (its relevant statement set grew -> new key)."""
    cp = tmp_path / "mc_cache.json"
    sc = _base_chain()
    k1 = mc_cache.mc_key(sc, "CTEST_A", samples=300, seed=42, percentiles=(5, 95))
    mc_cache.mc_for_class(sc, "CTEST_A", samples=300, cache_path=cp)

    # add a THIRD constituent statement to the SAME class
    sc.node("al", "Element", "Aluminium")
    sc.stmt("steel", "al", best=0.05, lo=0.03, hi=0.07, dist="triangular")

    k2 = mc_cache.mc_key(sc, "CTEST_A", samples=300, seed=42, percentiles=(5, 95))
    assert k1 != k2, "adding a statement to the class must change its cache key"
    res = mc_cache.mc_for_class(sc, "CTEST_A", samples=300, cache_path=cp)
    assert res["_cache"] == "miss"


def test_restate_value_invalidates(tmp_path):
    """Restating an existing (whole,part) with a DIFFERENT value mints a new
    content hash -> the relevant set changes -> miss."""
    cp = tmp_path / "mc_cache.json"
    sc = _base_chain()
    k1 = mc_cache.mc_key(sc, "CTEST_A", samples=300, seed=42, percentiles=(5, 95))

    sc2 = _base_chain()
    # same structure but a different best value on the copper statement
    sc2.stmts[0].best = 0.30
    k2 = mc_cache.mc_key(sc2, "CTEST_A", samples=300, seed=42, percentiles=(5, 95))
    assert k1 != k2


def test_identical_readd_still_hits(tmp_path):
    """A statement re-added with identical values has the SAME content hash, so
    the relevant set is unchanged -> the key is stable -> hit."""
    cp = tmp_path / "mc_cache.json"
    sc = _base_chain()
    mc_cache.mc_for_class(sc, "CTEST_A", samples=300, cache_path=cp)

    # re-add the copper statement with identical values (idempotent by hash)
    sc.stmt("steel", "cu", best=0.20, lo=0.15, hi=0.25, dist="triangular")
    res = mc_cache.mc_for_class(sc, "CTEST_A", samples=300, cache_path=cp)
    assert res["_cache"] == "hit"


def test_unrelated_change_keeps_hit(tmp_path):
    """Only the RELEVANT statements invalidate: a change confined to class B
    leaves A's key (hence A's cache) untouched."""
    cp = tmp_path / "mc_cache.json"
    sc = _other_chain_class()
    k_a = mc_cache.mc_key(sc, "CTEST_A", samples=300, seed=42, percentiles=(5, 95))
    mc_cache.mc_for_class(sc, "CTEST_A", samples=300, cache_path=cp)

    # change B's statement value — A must be unaffected
    sc.stmts[-1].best = 0.99
    k_a2 = mc_cache.mc_key(sc, "CTEST_A", samples=300, seed=42, percentiles=(5, 95))
    assert k_a == k_a2
    res = mc_cache.mc_for_class(sc, "CTEST_A", samples=300, cache_path=cp)
    assert res["_cache"] == "hit"


def test_param_change_invalidates(tmp_path):
    """The key folds in samples/seed/percentiles: changing samples is a miss."""
    cp = tmp_path / "mc_cache.json"
    sc = _base_chain()
    mc_cache.mc_for_class(sc, "CTEST_A", samples=300, cache_path=cp)
    res = mc_cache.mc_for_class(sc, "CTEST_A", samples=500, cache_path=cp)
    assert res["_cache"] == "miss"


def test_scoped_mc_has_an_interval(tmp_path):
    """Sanity: the cached MC actually carries a spread (lo < best < hi) for the
    constituents, i.e. the scoped aggregate_mc ran a real Monte-Carlo."""
    cp = tmp_path / "mc_cache.json"
    sc = _base_chain()
    res = mc_cache.mc_for_class(sc, "CTEST_A", samples=2000, cache_path=cp)
    body = {k: v for k, v in res.items() if k not in ("_cache", "_meta")}
    assert "Copper" in body and "Iron" in body
    for ec, iv in body.items():
        assert iv["lo"] <= iv["best"] <= iv["hi"]
        assert iv["lo"] < iv["hi"]          # a genuine interval, not collapsed

# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "pyshacl", "owlrl", "pytest"]
# ///
"""fq: projection — FULL coverage across ALL scenarios: the served fq: graph
reproduces the FROZEN oracle for every served quantity (element/part-in-whole,
unknown residual, SHACL), expected values derived INDEPENDENTLY of the resolver."""
import sys
import pathlib
from collections import defaultdict


import pytest

import scenarios
from common import pipeline
from builder import resolver
import served
Endpoint = served.Endpoint
from oracle.supplychain import FUT, LEVELS

PREFIX = f"""
PREFIX futuram: <{FUT}>
PREFIX fq: <https://www.purl.org/futuram/query#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""
APPROX = dict(rel=2e-3, abs=1e-6)

ALL_SIDS = sorted(scenarios.ALL)
PRODUCT, COMPONENT, MATERIAL, ELEMENT = LEVELS

# One endpoint per scenario, built once (the MC is expensive). Module-scoped.
_ENDPOINTS = {}


def ep_for(sid):
    if sid not in _ENDPOINTS:
        _ENDPOINTS[sid] = Endpoint(scenarios.ALL[sid])
    return _ENDPOINTS[sid]


# ---------------------------------------------------------------------------
# Independent oracle-derived expectations (no resolver code).
# ---------------------------------------------------------------------------
def _structural_parent(sc):
    return {s.part: s.whole for s in sc.stmts if not s.levels_skipped}


def _top_root(sc, node, parent=None):
    parent = parent or _structural_parent(sc)
    cur, seen = node, set()
    while cur in parent and cur not in seen:
        seen.add(cur); cur = parent[cur]
    return cur


def _part_total_in_root(sc, root, level, part_class):
    """Path-product of best fractions from root to (level, part_class) nodes,
    stopping at the match. The independent part-of mass derivation."""
    adj = defaultdict(list)
    for s in sc.stmts:
        if not s.levels_skipped:
            adj[s.whole].append((s.part, s.best_kgkg))
    total = [0.0]

    def walk(cur, acc):
        for part, frac in adj[cur]:
            f = acc * frac
            if sc.nodes[part].level == level and sc.nodes[part].cls == part_class:
                total[0] += f
            else:
                walk(part, f)
    walk(root, 1.0)
    return total[0]


def _expected_part_in_products(sc, level):
    """{ product_class: {part_class: frac} } for component/material-in-product,
    equal mean over the product class's instances (independent of resolver)."""
    sc._bind_levels()
    leaf = defaultdict(list)
    for r in sc._top_instances():
        leaf[sc.nodes[r].cls].append(r)
    part_classes = sorted({n.cls for n in sc.nodes.values() if n.level == level})
    out = {}
    for pcls, roots in leaf.items():
        n = len(roots)
        per = {}
        for pc in part_classes:
            acc = sum(_part_total_in_root(sc, r, level, pc) for r in roots)
            amt = acc / n if n else 0.0
            if amt > 1e-9:
                per[pc] = amt
        out[pcls] = per
    return out


def _served(ep, whole, level_local, part):
    """Query one served amount: the constituent's KIND is its rdfs:subClassOf
    edge to the level class (the flat fq:level marker is retired)."""
    rows = ep.query(PREFIX + f"""
        SELECT ?v WHERE {{ futuram:{whole} fq:contains ?a .
            ?a fq:constituent futuram:{part} ; fq:amount ?v .
            futuram:{part} rdfs:subClassOf futuram:{level_local} . }}""")
    rows = list(rows)
    return float(rows[0]["v"]) if rows else None


# ===========================================================================
# 1. Served graph well-formed for EVERY scenario
# ===========================================================================

@pytest.mark.parametrize("sid", ALL_SIDS)
def test_served_graph_conforms(sid):
    """Every scenario's projected fq: graph passes the fq: SHACL shapes."""
    g = ep_for(sid).served_graph()
    rep = pipeline.validate_served(g)
    assert rep.conforms, f"{sid}: served graph violates fq: shapes: {rep.messages[:5]}"


# ===========================================================================
# 2. Element-in-class == aggregate(), for EVERY class and element
# ===========================================================================

@pytest.mark.parametrize("sid", ALL_SIDS)
def test_element_in_class_matches_oracle(sid):
    """For every product/ancestor class the oracle aggregates, every element's
    served fq:amount equals aggregate()'s central value."""
    sc = scenarios.ALL[sid]
    ep = ep_for(sid)
    agg = sc.aggregate()
    checked = 0
    for cls, elems in agg.items():
        for ec, exp in elems.items():
            got = _served(ep, cls, ELEMENT, ec)
            assert got is not None, f"{sid}: no served element {ec} in {cls}"
            assert got == pytest.approx(exp, **APPROX), \
                f"{sid}: element {ec} in {cls}: served {got} != oracle {exp}"
            checked += 1
    assert checked > 0, f"{sid}: no element-in-class checks ran"


# ===========================================================================
# 3. Component-in-product and Material-in-product == path-product, EVERY pair
# ===========================================================================

@pytest.mark.parametrize("sid", ALL_SIDS)
def test_component_in_product_matches_oracle(sid):
    sc = scenarios.ALL[sid]
    ep = ep_for(sid)
    expected = _expected_part_in_products(sc, COMPONENT)
    for pcls, comps in expected.items():
        for ccls, exp in comps.items():
            got = _served(ep, pcls, COMPONENT, ccls)
            assert got is not None, f"{sid}: no served component {ccls} in {pcls}"
            assert got == pytest.approx(exp, **APPROX), \
                f"{sid}: component {ccls} in {pcls}: served {got} != oracle {exp}"


@pytest.mark.parametrize("sid", ALL_SIDS)
def test_material_in_product_matches_oracle(sid):
    sc = scenarios.ALL[sid]
    ep = ep_for(sid)
    expected = _expected_part_in_products(sc, MATERIAL)
    for pcls, mats in expected.items():
        for mcls, exp in mats.items():
            got = _served(ep, pcls, MATERIAL, mcls)
            assert got is not None, f"{sid}: no served material {mcls} in {pcls}"
            assert got == pytest.approx(exp, **APPROX), \
                f"{sid}: material {mcls} in {pcls}: served {got} != oracle {exp}"


# ===========================================================================
# 4. Unknown residual + coverage == oracle, for EVERY unknown scenario
# ===========================================================================

UNKNOWN_SIDS = sorted(
    sid for sid, sc in scenarios.ALL.items()
    if any(d["unknown_min"] > 1e-9 for d in sc.coarse_fine().values()))


@pytest.mark.parametrize("sid", UNKNOWN_SIDS)
def test_unknown_remainder_is_a_first_class_constituent(sid):
    """The unattributed remainder is surfaced as an EXPLICIT constituent of its
    level's unknown* class (NOT an fq:unknownAmount attribute on a named part), so
    parts at a level sum to 1.0. Replaces the retired fq:unknownAmount."""
    sc = scenarios.ALL[sid]
    ep = ep_for(sid)
    g = ep.served_graph()
    FQ = resolver.FQ
    FUT = resolver.FUT
    from rdflib import RDFS
    # An unknown* constituent is EITHER a shared placeholder class OR a per-context
    # minted holder (unknown<Level>_in_<whole>) declared rdfs:subClassOf one of them.
    # Recognise via the subClassOf-to-placeholder edge, not an exact-IRI whitelist.
    placeholders = {FUT[resolver.UNKNOWN_COMPONENT], FUT[resolver.UNKNOWN_MATERIAL],
                    FUT[resolver.UNKNOWN_ELEMENT]}
    unknown_iris = set(placeholders)
    for s, _, o in g.triples((None, RDFS.subClassOf, None)):
        if o in placeholders:
            unknown_iris.add(s)               # minted per-context holder

    # At least one unknown* constituent is served for a scenario with a residual.
    served_unknown_nodes = [
        a for a in g.subjects(FQ.constituent, None)
        if next(g.objects(a, FQ.constituent)) in unknown_iris
    ]
    assert served_unknown_nodes, \
        f"{sid}: expected an unknown* constituent (placeholder or minted), found none"

    # Each unknown* constituent carries a positive amount and the disjointness-safe
    # level self-typing: its element is rdfs:subClassOf EXACTLY ONE of the four
    # level classes (the ONLY kind marker — fq:level is retired).
    level_classes = {FUT[lv] for lv in LEVELS}
    for a in served_unknown_nodes:
        amt = float(next(g.objects(a, FQ.amount)))
        assert amt > 0.0, f"{sid}: unknown constituent amount {amt} not positive"
        elem = next(g.objects(a, FQ.constituent))
        lvls = set(g.objects(elem, resolver.RDFS.subClassOf)) & level_classes
        assert len(lvls) == 1, \
            f"{sid}: unknown constituent {elem} must subClassOf exactly one level, got {lvls}"

    # The retired representations must be gone: no fq:unknownAmount, no fq:level.
    assert not list(g.triples((None, FQ.unknownAmount, None))), \
        f"{sid}: fq:unknownAmount is retired — unknown is a constituent row now"
    assert not list(g.triples((None, FQ.level, None))), \
        f"{sid}: fq:level is retired — the kind lives only in rdfs:subClassOf"


# ===========================================================================
# 4a'. OMIT -> RE-INFER is VALUE-PRESERVING. The ETL omits the element-cell
# remainder, so named fractions sum < 1.0 and balance() re-infers the residual as
# futuram:unknownElement losslessly: named + unknownElement must sum to EXACTLY 1.0.
# ===========================================================================

@pytest.mark.parametrize("sid", UNKNOWN_SIDS)
def test_element_remainder_reinference_is_value_preserving(sid):
    ep = ep_for(sid)
    g = ep.served_graph()
    FQ = resolver.FQ
    FUT = resolver.FUT
    from rdflib import RDFS

    elem_level = FUT[ELEMENT]
    unknown_elem = FUT[resolver.UNKNOWN_ELEMENT]
    # the per-context minted element holders are rdfs:subClassOf unknownElement.
    unknown_elem_iris = {unknown_elem} | {
        s for s, _, o in g.triples((None, RDFS.subClassOf, unknown_elem))}

    # sum served ELEMENT-level amounts per whole; note wholes carrying a
    # re-inferred unknownElement constituent.
    elem_total = defaultdict(float)        # whole -> Σ all element fractions
    named_total = defaultdict(float)       # whole -> Σ NAMED element fractions
    unknown_amt = defaultdict(float)       # whole -> Σ re-inferred unknownElement
    for a in g.subjects(FQ.constituent, None):
        elem = next(g.objects(a, FQ.constituent))
        if (elem, RDFS.subClassOf, elem_level) not in g:
            continue                                  # not an Element constituent
        whole = next(g.objects(a, FQ.whole))
        amt = float(next(g.objects(a, FQ.amount)))
        elem_total[whole] += amt
        if elem in unknown_elem_iris:
            unknown_amt[whole] += amt
        else:
            named_total[whole] += amt

    assert elem_total, f"{sid}: no Element-level constituents served"
    # Where a conserving whole's named element coverage falls short of 1.0, the
    # gap MUST be carried by a re-inferred unknownElement. Keyed to an actual
    # element gap (some SIDs hold their residual at material/component level).
    gap_wholes = [w for w, nt in named_total.items()
                  if elem_total[w] <= 1.0 + 1e-3 and nt < 1.0 - 1e-3]
    for w in gap_wholes:
        assert unknown_amt[w] > 1e-9, (
            f"{sid}: {str(w).split('#')[-1]} named elements cover {named_total[w]} "
            f"< 1.0 but no unknownElement was re-inferred to close the gap")
    # value-preserving: a conserving whole's element fractions sum to EXACTLY 1.0.
    # EXCEPTION — overflow/overshoot wholes (08/19/22b) encode a coarse bound above the
    # granular paths, so >1.0 is intended; skip them (covered by overshoot/coarse_fine).
    checked = 0
    for whole, total in elem_total.items():
        if total > 1.0 + 1e-3:                # an intended overflow whole — skip
            continue
        checked += 1
        assert total == pytest.approx(1.0, **APPROX), (
            f"{sid}: element constituents of {str(whole).split('#')[-1]} sum to "
            f"{total}, not 1.0 — omit->re-infer lost or invented element mass")
    assert checked, f"{sid}: every whole overflowed — no conserving whole to check"


# ===========================================================================
# 4b. COMPLETENESS — a minted unknown holder with positive mass decomposes to
#     the next level (recursively to Element), and NO level is skipped (a
#     Component-level holder routes through a Material, never directly an Element).
# ===========================================================================

# scenarios whose coarse bounds INFER downstream content under an unknown holder
# (a minted unknown<Level>_in_<whole> chain). A plain residual with no inferable
# downstream class produces only shared rows, so it is NOT in scope here.
def _has_inferred(sc):
    from builder.resolver.context import ResolverContext
    from builder.resolver.plugins.partof import _inferred_unknown_content
    ctx = ResolverContext(sc.to_graph())
    return any(plans for plans in _inferred_unknown_content(ctx).values())


INFER_SIDS = sorted(sid for sid, sc in scenarios.ALL.items() if _has_inferred(sc))


@pytest.mark.parametrize("sid", INFER_SIDS)
def test_minted_unknown_holders_decompose_without_level_skip(sid):
    """Every minted per-context unknown holder with positive mass has a child at
    the NEXT level down (recursively) and NO level is skipped — guarding the
    BNode/level-skip bug (Component holder routes through a Material to Element)."""
    from rdflib import RDFS
    sc = scenarios.ALL[sid]
    ep = ep_for(sid)
    g = ep.served_graph()
    FQ = resolver.FQ
    FUT = resolver.FUT
    # placeholder unknown class -> its level class (futuram:unknownMaterial -> Material)
    ph_level = {FUT[resolver.UNKNOWN_COMPONENT]: FUT[COMPONENT],
                FUT[resolver.UNKNOWN_MATERIAL]: FUT[MATERIAL],
                FUT[resolver.UNKNOWN_ELEMENT]: FUT[ELEMENT]}
    placeholders = set(ph_level)
    # minted holders only (the per-context unknown<Level>_in_<whole> classes)
    minted = {s for s, _, o in g.triples((None, RDFS.subClassOf, None))
              if o in placeholders and s not in placeholders}
    assert minted, f"{sid}: expected at least one minted unknown holder"

    # the next level down for each level class (Product->Component->Material->Element)
    nxt = {FUT[lv]: (FUT[LEVELS[i + 1]] if i + 1 < len(LEVELS) else None)
           for i, lv in enumerate(LEVELS)}

    saw_decomposition = False
    for holder in minted:
        # the holder's own level = the placeholder it subclasses
        ph = next(o for _, _, o in g.triples((holder, RDFS.subClassOf, None))
                  if o in placeholders)
        holder_level = ph_level[ph]                           # unknown<Level> -> <Level>
        child_level = nxt[holder_level]
        children = list(g.objects(holder, FQ.contains))
        if not children:
            continue                          # element-level holder: terminal leaf
        saw_decomposition = True
        level_classes = {FUT[lv] for lv in LEVELS}
        for a in children:
            # the child's level = its element's subClassOf-to-level edge (the
            # ONLY kind marker; fq:level is retired)
            child_elem = next(g.objects(a, FQ.constituent))
            lvls = set(g.objects(child_elem, RDFS.subClassOf)) & level_classes
            assert len(lvls) == 1, \
                f"{sid}: child {child_elem} must subClassOf exactly one level, got {lvls}"
            lvl = next(iter(lvls))
            # NO SKIP: every child sits exactly one level below the holder.
            assert lvl == child_level, (
                f"{sid}: {resolver._local(holder)} (level {resolver._local(holder_level)}) "
                f"has a child at {resolver._local(lvl)}, expected {resolver._local(child_level)} "
                f"— level skip / decomposition gap")
            amt = float(next(g.objects(a, FQ.amount)))
            assert amt > 0.0, f"{sid}: zero-mass child under {resolver._local(holder)}"
    assert saw_decomposition, \
        f"{sid}: no minted unknown holder decomposed to the next level"


# ===========================================================================
# 5. Live == materialized for EVERY scenario (wrapper transparency)
# ===========================================================================

@pytest.mark.parametrize("sid", ALL_SIDS)
def test_live_equals_materialized(sid):
    """A representative query gives identical answers from a live endpoint and a
    materialize_all endpoint, for every scenario."""
    sc = scenarios.ALL[sid]
    agg = sc.aggregate()
    cls = next(iter(agg))
    ec = next(iter(agg[cls]))
    q = PREFIX + f"""SELECT ?v WHERE {{ futuram:{cls} fq:contains ?a .
        ?a fq:constituent futuram:{ec} ; fq:amount ?v .
        futuram:{ec} rdfs:subClassOf futuram:Element . }}"""
    live = float(list(Endpoint(sc).query(q))[0]["v"])
    mat = float(list(Endpoint(sc, materialize_all=True).query(q))[0]["v"])
    assert live == pytest.approx(mat, **APPROX)

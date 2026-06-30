# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "owlrl", "pytest"]
# ///
"""RDF pipeline layer — SHACL shapes + SPARQL rules are the system under test; the
Python ORACLE is the expected answer (every numeric expectation comes from it). Rule
tests RDFS-close the graph (graph_of(rdfs=True)) so the futuram level types exist."""
import sys
import pathlib


import pytest
from rdflib import Graph, RDF, RDFS
from oracle.supplychain import SupplyChain, FUT
import scenarios
import conftest as H
from common import pipeline


def graph_of(scenario, full_metadata=True, rdfs=False):
    """TBox + hierarchy + the scenario graph (id str or SupplyChain). rdfs=True
    RDFS-closes the graph so the futuram level types are materialised. Delegates
    to pipeline.build_graph, shared with the fq: projection."""
    sc = scenarios.ALL[scenario] if isinstance(scenario, str) else scenario
    g = pipeline.build_graph(sc, full_metadata=full_metadata, rdfs=rdfs)
    return g, sc


# ===========================================================================
# L1 — SHACL well-formedness
# ===========================================================================

def test_L1_all_scenarios_wellformed_conform():
    """Every scenario, with full metadata, is structurally well-formed. The only
    expected violations are conservation/coarse (L2/L5), so assert NO
    well-formedness (metadata/unit/role) violations appear."""
    wellformed_keywords = ("per 1 kg", "must have", "must be", "must record",
                           "must point", "leaf", "individual")
    for sid, sc in scenarios.ALL.items():
        g, _ = graph_of(sc)
        conforms, msgs = H.shacl(g)
        bad = [m for m in msgs if any(k in m for k in wellformed_keywords)]
        assert not bad, f"{sid}: unexpected well-formedness violations: {bad}"


def test_L1_bare_kg_rejected():
    """A statement quantity in absolute kg (not a fraction) violates
    'per 1 kg of the whole'."""
    sc = SupplyChain("bare kg")
    sc.provenance = {"source": "test", "agent": "test", "production": "forming",
                     "validFrom": "2020-01-01"}
    sc.node("motor", "Component", "elvElectricMotor")
    sc.node("cu", "Material", "pureCu")
    s = sc.stmt("motor", "cu", 0.1, 0.2)
    from oracle.supplychain import UNIT
    s.unit = UNIT["KiloGM"]                     # illegal: absolute mass
    g = Graph(); g.parse(H.TBOX, format="turtle"); g.parse(H.HIERARCHY, format="turtle")
    g += sc.to_graph(full_metadata=False)
    conforms, msgs = H.shacl(g)
    assert not conforms
    assert any("per 1 kg" in m for m in msgs)


def test_L1_missing_metadata_rejected():
    """Stripping required metadata (full_metadata=False) must fail SHACL."""
    g, _ = graph_of("01_simple_conserving", full_metadata=False)
    conforms, msgs = H.shacl(g)
    assert not conforms
    assert any("must have" in m or "must record" in m for m in msgs)


# ===========================================================================
# L2 — Mass conservation (single-level), via check-mass-conservation.rq
# ===========================================================================

@pytest.mark.parametrize("sid", [k for k in scenarios.ALL])
def test_L2_conservation_matches_oracle(sid):
    """The conservation rule flags massConserved=false on exactly the wholes the
    oracle says overshoot."""
    g, sc = graph_of(sid, rdfs=True)
    H.run_rule(g, H.RULE_CONSERVATION)
    flagged = {str(s).split("#")[-1].split("/")[-1]
               for s, p, o in g.triples((None, FUT.massConserved, None))}
    expected = {w for w, d in sc.conservation().items() if d["overshoot"]}
    assert flagged == expected, f"{sid}: flagged {flagged} expected {expected}"


@pytest.mark.parametrize("sid", [k for k in scenarios.ALL])
def test_L2_overshoot_rejected_by_shacl(sid):
    """After the conservation rule, SHACL conforms iff the oracle says no whole
    overshoots."""
    g, sc = graph_of(sid, rdfs=True)
    H.run_rule(g, H.RULE_CONSERVATION)
    conforms, msgs = H.shacl(g)
    any_overshoot = any(d["overshoot"] for d in sc.conservation().values())
    conservation_violation = any("not conserved" in m for m in msgs)
    assert conservation_violation == any_overshoot, (
        f"{sid}: shacl conservation-violation={conservation_violation} "
        f"oracle overshoot={any_overshoot}")


# ===========================================================================
# L3 — Class composition lift, via infer-class-composition.rq
# ===========================================================================

def test_L3_lift_produces_class_composition():
    """A motor->material instance statement lifts to (motor class)
    hasComposition (material class). Scenario 01's motor is made of
    highCuAlloys (the honest class for its copper-nickel winding)."""
    g, sc = graph_of("01_simple_conserving", rdfs=True)
    H.run_rule(g, H.RULE_LIFT)
    comps = set(g.triples((None, FUT.hasComposition, None)))
    assert comps, "lift produced no hasComposition triples"
    pairs = {(str(s).split("#")[-1], str(o).split("#")[-1]) for s, p, o in comps}
    assert ("elvElectricMotor", "highCuAlloys") in pairs


# ===========================================================================
# L4 — Chain completeness, via complete-chains.rq
# ===========================================================================

# scenarios with at least one coarse (level-skipping) statement
COARSE_SCENARIOS = sorted(
    sid for sid, sc in scenarios.ALL.items()
    if any(s.levels_skipped for s in (sc._bind_levels() or sc.stmts))
)


_RANK = {"Product": 0, "Component": 1, "Material": 2, "Element": 3}


@pytest.mark.parametrize("sid", COARSE_SCENARIOS)
def test_L4_coarse_statement_gets_unknown_fillers(sid):
    """Every coarse statement is completed with one unknown filler PER strictly-
    skipped level (Component and/or Material), so Product->Element yields both,
    Product->Material yields a Component, Component->Element yields a Material."""
    g, sc = graph_of(sid, rdfs=True)
    H.run_rule(g, str(H.RULE_COMPLETE_CHAINS))
    sc._bind_levels()
    exp_comp = exp_mat = 0
    for s in sc.stmts:
        if not s.levels_skipped:
            continue
        wr, pr = _RANK[sc.nodes[s.whole].level], _RANK[sc.nodes[s.part].level]
        levels_between = set(range(wr + 1, pr))   # strictly skipped level ranks
        exp_comp += 1 if _RANK["Component"] in levels_between else 0
        exp_mat += 1 if _RANK["Material"] in levels_between else 0
    uc = len(list(g.triples((None, None, FUT.unknownComponent))))
    um = len(list(g.triples((None, None, FUT.unknownMaterial))))
    assert uc == exp_comp, f"{sid}: unknownComponent {uc} != {exp_comp}"
    assert um == exp_mat, f"{sid}: unknownMaterial {um} != {exp_mat}"


# L4b — CLASS composition is strict one-step-down, instances stay coarse.
# ===========================================================================

@pytest.mark.parametrize("sid", COARSE_SCENARIOS)
def test_L4b_class_view_has_no_level_skips(sid):
    """The derived CLASS composition (futuram:hasComposition) is one step at a
    time (no Product→Element skips): a coarse instance edge routes through the
    filler chain. Checked structurally AND via the SHACL adjacency shape."""
    g, sc = graph_of(sid, rdfs=False)
    pipeline.materialize(g)

    def class_level(c):
        for root, r in _RANK.items():
            if c == FUT[root] or (c, RDFS.subClassOf, FUT[root]) in g:
                return r
        for o in g.objects(c, RDFS.subClassOf):
            for root, r in _RANK.items():
                if o == FUT[root] or (o, RDFS.subClassOf, FUT[root]) in g:
                    return r
        return None

    skips = []
    for w, p in g.subject_objects(FUT.hasComposition):
        lw, lp = class_level(w), class_level(p)
        if lw is not None and lp is not None and lp - lw >= 2:
            skips.append((w, p))
    assert not skips, f"{sid}: class-level hasComposition level-skips: {skips[:4]}"

    # the SHACL adjacency shape agrees (no class-view skip violations)
    conforms, msgs = H.shacl(g)
    assert not any("Level skip in the CLASS view" in m for m in msgs), \
        f"{sid}: SHACL flagged a class-view level skip"


@pytest.mark.parametrize("sid", COARSE_SCENARIOS)
def test_L4b_instance_coarse_statement_survives(sid):
    """The INSTANCE level is NOT made strict: a real coarse measurement (a
    part relation ≥2 levels apart) stays on the A-Box. The class view is
    strict; the instances may say what they measured."""
    g, sc = graph_of(sid, rdfs=False)
    pipeline.materialize(g)
    def inst_level(n):
        # the instance's level = the root its asserted type sits under
        # (rdf:type then rdfs:subClassOf* to a level root).
        for t in g.objects(n, RDF.type):
            for root, r in _RANK.items():
                if t == FUT[root] or (t, RDFS.subClassOf, FUT[root]) in g:
                    return r
        return None

    # at least one reified relation still skips a level (the original coarse one).
    # New shape: whole -hasCompositionStatement-> cs -hasPartRelation-> rel -refersTo-> part.
    found = False
    for rel in g.subjects(RDF.type, FUT.PartRelation):
        cs = next(iter(g.subjects(FUT.hasPartRelation, rel)), None)
        w = g.value(predicate=FUT.hasCompositionStatement, object=cs) if cs else None
        p = g.value(rel, FUT.refersTo)
        if w is None or p is None:
            continue
        lw, lp = inst_level(w), inst_level(p)
        if lw is not None and lp is not None and lp - lw >= 2:
            found = True
            break
    assert found, f"{sid}: expected the coarse instance statement to survive materialize"


# ===========================================================================
# L5 — Coarse/fine reconciliation, via propagate-granular.rq (fixpoint) +
#      reconcile-coarse-fine.rq
# ===========================================================================

def _reconcile(sid):
    """Run the full unknowns pipeline and return (graph, oracle): propagate the
    granular path-products to a fixpoint, then reconcile each coarse bound.
    Delegates to pipeline.reconcile; graph_of already RDFS-closed (rdfs=False)."""
    g, sc = graph_of(sid, rdfs=True)
    pipeline.reconcile(g, rdfs=False)
    return g, sc


# every scenario that has at least one POSITIVE unknown residual
UNKNOWN_SCENARIOS = sorted(
    sid for sid, sc in scenarios.ALL.items()
    if any(d["unknown_min"] > 1e-9 for d in sc.coarse_fine().values())
)


@pytest.mark.parametrize("sid", UNKNOWN_SCENARIOS)
def test_L5_unknown_residual_matches_oracle(sid):
    """The reconcile rule derives, for each coarse bound, an unknownAmount equal
    to the oracle's unknown_min (coarse - granular path-product) — at any depth,
    thanks to the fixpoint propagation."""
    g, sc = _reconcile(sid)
    rule_unknowns = sorted(round(float(o), 6)
                           for s, p, o in g.triples((None, FUT.unknownAmount, None))
                           if float(o) > 1e-9)
    oracle_unknowns = sorted(round(d["unknown_min"], 6)
                             for d in sc.coarse_fine().values()
                             if d["unknown_min"] > 1e-9)
    assert rule_unknowns == oracle_unknowns, (
        f"{sid}: rule {rule_unknowns} != oracle {oracle_unknowns}")


@pytest.mark.parametrize("sid", [k for k in scenarios.ALL])
def test_L5_cross_level_conservation_matches_oracle(sid):
    """The reconcile rule stamps massConserved=false exactly when the granular
    detail over-claims an element vs its coarse ceiling (oracle 'overshoot' on a
    coarse pair)."""
    g, sc = _reconcile(sid)
    rule_violation = any(str(o) == "false"
                         for s, p, o in g.triples((None, FUT.massConserved, None)))
    oracle_overshoot = any(d["overshoot"] for d in sc.coarse_fine().values())
    assert rule_violation == oracle_overshoot, (
        f"{sid}: rule cross-level violation={rule_violation} "
        f"oracle overshoot={oracle_overshoot}")


def test_L5_cross_level_overshoot_rejected_by_shacl():
    """09_fine_gt_coarse: granular paths exceed the coarse ceiling -> the
    reconcile rule's massConserved=false makes SHACL reject the graph."""
    g, sc = _reconcile("09_fine_gt_coarse")
    conforms, msgs = H.shacl(g)
    assert not conforms
    assert any("not conserved" in m for m in msgs)

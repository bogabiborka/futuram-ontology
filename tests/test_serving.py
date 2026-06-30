# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "pyshacl", "owlrl", "pytest"]
# ///
"""Acceptance tests for the projected `fq:` query interface (the flat, class-only
view derived by the resolver plugins). Each test pairs a SPARQL query against the fq:
endpoint with the frozen oracle number (sc.aggregate()/aggregate_mc()) it must equal."""
import sys
import pathlib


import pytest

import scenarios
from builder import resolver
from rdflib import Namespace
from oracle.supplychain import FUT

FQ = Namespace("https://www.purl.org/futuram/query#")

# `Endpoint` is the thin resolve-then-query wrapper (wrapper, not custom-eval):
# given a scenario's SupplyChain it answers SPARQL over the
# `fq:` virtual ontology, resolving class-level composition on the fly.
import served
Endpoint = served.Endpoint


# Standard PREFIX header every served query carries.
PREFIX = f"""
PREFIX futuram: <{FUT}>
PREFIX fq: <https://www.purl.org/futuram/query#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""

APPROX = dict(rel=1e-3)   # MC is seeded (seed=42) so results are reproducible.


def endpoint_for(sid):
    """A virtual `fq:` endpoint backed by one scenario's frozen-oracle SupplyChain."""
    return Endpoint(scenarios.ALL[sid])


def one(rows):
    """Exactly one result row; fail loudly otherwise (catches empty = unresolved)."""
    rows = list(rows)
    assert len(rows) == 1, f"expected exactly 1 row, got {len(rows)}: {rows}"
    return rows[0]


# ===========================================================================
# A. Core pattern — "how much Element E in CLASS X" (plain class scope)
#    Oracle: aggregate (central) / aggregate_mc (interval).
# ===========================================================================

def test_A1_copper_in_class_best_matches_oracle():
    """The headline `fq:amount` for (V0301030105, Copper) equals the oracle's
    central aggregate. One shallow BGP, subject is a CLASS."""
    sc = scenarios.ALL["20_multi_instance_class"]
    expected = sc.aggregate()["V0301030105"]["Copper"]
    ep = Endpoint(sc)
    row = one(ep.query(PREFIX + """
        SELECT ?v WHERE {
          futuram:V0301030105 fq:contains ?a .
          ?a fq:constituent futuram:Copper ; fq:amount ?v .
        }"""))
    assert float(row["v"]) == pytest.approx(expected, **APPROX)


def test_A2_interval_matches_oracle_mc_percentiles():
    """With MC ENABLED (with_mc=True — opt-in), `fq:amountLow`/`fq:amountHigh`
    are the MC 5/95 percentiles; `fq:amount` stays the deterministic aggregate()
    central. (MC is OFF by default — the first-draft fast path emits best only.)"""
    sc = scenarios.ALL["21_multi_instance_mc"]
    mc = sc.aggregate_mc()
    agg = sc.aggregate()
    cls = next(iter(mc))                       # any class with a spread
    elem = next(iter(mc[cls]))
    exp = mc[cls][elem]
    ep = Endpoint(sc, with_mc=True)            # opt into the MC spread
    row = one(ep.query(PREFIX + f"""
        SELECT ?v ?lo ?hi WHERE {{
          futuram:{cls} fq:contains ?a .
          ?a fq:constituent futuram:{elem} ;
             fq:amount ?v ; fq:amountLow ?lo ; fq:amountHigh ?hi .
        }}"""))
    assert float(row["v"])  == pytest.approx(agg[cls][elem], **APPROX)  # deterministic best
    assert float(row["lo"]) == pytest.approx(exp["lo"],   **APPROX)
    assert float(row["hi"]) == pytest.approx(exp["hi"],   **APPROX)


def test_A3_element_enumeration_matches_oracle():
    """Enumerating every ELEMENT in a class returns exactly the oracle's element
    set with matching best values. The product also fq:contains components/materials,
    so the query declares the KIND via `?e rdfs:subClassOf futuram:Element`."""
    sc = scenarios.ALL["24_multi_class"]
    expected = sc.aggregate()["V0301030105"]          # {element_class: amount}
    ep = Endpoint(sc)
    rows = ep.query(PREFIX + """
        SELECT ?e ?v WHERE {
          futuram:V0301030105 fq:contains ?a .
          ?a fq:constituent ?e ; fq:amount ?v .
          ?e rdfs:subClassOf futuram:Element .
        }""")
    got = {str(r["e"]).split("#")[-1]: float(r["v"]) for r in rows}
    # Served set = oracle NAMED elements PLUS the auto-balance unknownElement filler
    # (1.0 − Σ named). Compare the named elements; the filler is verified separately.
    named = {e: v for e, v in got.items() if e != "unknownElement"}
    assert set(named) == set(expected), f"elements {set(named)} != {set(expected)}"
    for e, v in expected.items():
        assert named[e] == pytest.approx(v, **APPROX)
    # the filler closes the balance to 1.0 (when there's a shortfall)
    assert sum(got.values()) == pytest.approx(1.0, abs=1e-6) or "unknownElement" not in got


def test_A4_subject_is_a_class_not_instance():
    """The served subject is a CLASS: the served graph never exposes a bare
    instance node as an fq:contains subject (the fq: view is class-only)."""
    sc = scenarios.ALL["20_multi_instance_class"]
    ep = Endpoint(sc)
    # The class HAS an answer ...
    cls_rows = list(ep.query(PREFIX + """
        SELECT ?v WHERE { futuram:elvBEV fq:contains ?a .
                          ?a fq:constituent futuram:Copper ; fq:amount ?v . }"""))
    assert len(cls_rows) == 1
    # ... and every fq:contains subject in the served graph is typed a class.
    assert served.every_contains_subject_is_a_class(ep), \
        "an fq:contains subject was not a class (violates class-only serving)"


# ===========================================================================
# B. KEYSTONE — narrow scope vs broad scope MUST differ: "copper in V0301030105"
#    (one car class) vs "copper in elvBEV" (the equal-mean of its sub-products).
#    24_multi_class: V05 Cu=0.49157, V01 Cu=0.285, elvBEV Cu=0.38829 (their mean).
# ===========================================================================

def test_B1_narrow_class_differs_from_parent_class():
    """The SAME fq:contains query against a sub-product class and against its
    parent segment class yields DIFFERENT values — each equal to its own oracle
    aggregate. This is the whole point: scope changes the answer."""
    sc = scenarios.ALL["24_multi_class"]
    agg = sc.aggregate()
    exp_narrow = agg["V0301030105"]["Copper"]
    exp_broad  = agg["elvBEV"]["Copper"]
    assert exp_narrow != pytest.approx(exp_broad, **APPROX), \
        "scenario precondition: the two scopes must have different oracle values"
    ep = Endpoint(sc)

    def cu_in(cls):
        return float(one(ep.query(PREFIX + f"""
            SELECT ?v WHERE {{ futuram:{cls} fq:contains ?a .
                ?a fq:constituent futuram:Copper ; fq:amount ?v . }}"""))["v"])

    narrow = cu_in("V0301030105")
    broad  = cu_in("elvBEV")
    assert narrow == pytest.approx(exp_narrow, **APPROX)
    assert broad  == pytest.approx(exp_broad,  **APPROX)
    assert narrow != pytest.approx(broad, **APPROX)   # the two calls MUST differ


def _motor_copper(sc, restrict_to_product=None):
    """Independently derive 'copper in elvElectricMotor' from the FROZEN oracle,
    optionally scoped to a product class (None=all motors), so the B2 expectation
    is non-circular (no resolver code; same equal-mean rollup as aggregate())."""
    sc._bind_levels()

    def top_root_and_cls(node):
        parents = {s.part: s.whole for s in sc.stmts if not s.levels_skipped}
        cur, seen = node, set()
        while cur in parents and cur not in seen:
            seen.add(cur); cur = parents[cur]
        return cur, sc.nodes[cur].cls

    motors = []
    for nm, n in sc.nodes.items():
        if n.cls == "elvElectricMotor_Y2020":
            root, tcls = top_root_and_cls(nm)
            if restrict_to_product is None or tcls == restrict_to_product:
                motors.append((nm, root))
    cnt = len(motors)
    return sum(sc.element_in_whole(nm, "Copper")
               for nm, r in motors) / cnt if cnt else 0.0


def test_B2_contextual_partof_scope_differs():
    """THE KEYSTONE: a COMPONENT scoped to a PRODUCT differs from the component
    unscoped. 'copper in a motor' (all motors) vs 'copper in a motor OF V0301030101'
    differ and can't be a subclass query (no V01Motor); scope is a partOf constraint."""
    sc = scenarios.ALL["24_multi_class"]
    exp_unscoped = _motor_copper(sc, restrict_to_product=None)
    exp_scoped = _motor_copper(sc, restrict_to_product="V0301030101_Y2020")
    assert exp_unscoped != pytest.approx(exp_scoped, **APPROX), \
        "scenario precondition: the motor-in-V01 scope must differ from all-motors"
    ep = Endpoint(sc)

    # unscoped: copper in a motor (any motor)
    unscoped = float(one(ep.query(PREFIX + """
        SELECT ?v WHERE { futuram:elvElectricMotor_Y2020 fq:contains ?a .
            ?a fq:constituent futuram:Copper ; fq:amount ?v . }"""))["v"])

    # contextual: copper in a motor OF (partOf) a V0301030101 product
    scoped = float(one(ep.query(PREFIX + """
        SELECT ?v WHERE {
          ?m a futuram:elvElectricMotor_Y2020 ;
             futuram:partOf futuram:V0301030101_Y2020 .
          ?m fq:contains ?a .
          ?a fq:constituent futuram:Copper ; fq:amount ?v .
        }"""))["v"])

    assert unscoped == pytest.approx(exp_unscoped, **APPROX)
    assert scoped == pytest.approx(exp_scoped, **APPROX)
    assert scoped != pytest.approx(unscoped, **APPROX)   # context changes the answer


# ===========================================================================
# C. Unknown remainder surfaced as an explicit unknown* constituent row
#    Oracle: coarse_fine() unknown residuals.
# ===========================================================================

def _expected_class_unknown(sc, top_class, element_class):
    """Independently derive the CLASS-LEVEL unknown for (top_class, element_class)
    from the FROZEN oracle: each coarse bound's unknown_min projected per-kg-of-top
    by path fraction, equal-mean over instances. Non-circular (no resolver code)."""
    sc._bind_levels()

    def top_and_frac(node):
        parent, fr = {}, {}
        for s in sc.stmts:
            if not s.levels_skipped:
                parent[s.part] = s.whole
                fr[s.part] = s.lo_kgkg
        cur, f, seen = node, 1.0, set()
        while cur in parent and cur not in seen:
            seen.add(cur); f *= fr[cur]; cur = parent[cur]
        return cur, f

    per_root = {}
    for (whole_node, elem_node), d in sc.coarse_fine().items():
        u = d.get("unknown_min", 0.0)
        if u <= 1e-12 or sc.nodes[elem_node].cls != element_class:
            continue
        top, frac = top_and_frac(whole_node)
        per_root[top] = per_root.get(top, 0.0) + u * frac

    roots = [r for r in sc._top_instances() if sc.nodes[r].cls == top_class]
    if not roots:
        return 0.0
    n = len(roots)
    return sum(per_root.get(r, 0.0) for r in roots) / n if n else 0.0


def test_C1_unknown_remainder_is_an_unknownElement_row_not_an_attribute():
    """A positive element-level residual surfaces as an EXPLICIT unknownElement
    constituent (⊑ Element) balancing the Element axis to 1.0; the retired
    fq:unknownAmount/fq:coverage are absent and Nickel carries only its aggregate."""
    sc = scenarios.ALL["20b_multi_instance_unknowns"]
    exp_unknown = _expected_class_unknown(sc, "V0301030105_Y2020", "Nickel")
    assert exp_unknown > 1e-9, "scenario precondition: a positive class unknown exists"
    ep = Endpoint(sc)
    g = ep.served_graph()

    # 1) the retired attributes are GONE everywhere in the served graph.
    assert not list(g.triples((None, FQ.unknownAmount, None))), \
        "fq:unknownAmount is retired — the unknown is a constituent row now"
    assert not list(g.triples((None, FQ.coverage, None))), \
        "fq:coverage is retired — the unknown is a constituent row now"

    # 2) the Element axis of V0301030105 carries a positive unknownElement row and
    #    sums to 1.0 (the named elements PLUS the unknownElement filler).
    rows = list(ep.query(PREFIX + """
        SELECT ?e ?v WHERE {
          futuram:V0301030105 fq:contains ?a .
          ?a fq:constituent ?e ; fq:amount ?v .
          ?e rdfs:subClassOf futuram:Element . }"""))
    by_elem = {str(r["e"]).split("#")[-1]: float(r["v"]) for r in rows}
    assert by_elem.get("unknownElement", 0.0) > 0.0, \
        "expected a positive futuram:unknownElement remainder row on the Element axis"
    assert sum(by_elem.values()) == pytest.approx(1.0, abs=1e-6), \
        f"Element axis should balance to 1.0 with the unknownElement filler: {by_elem}"

    # 3) Nickel is its attributed aggregate (not inflated); the unknownElement
    #    constituent self-types ⊑ Element (the subClassOf edge is the ONLY kind
    #    marker — fq:level is retired).
    assert by_elem["Nickel"] == pytest.approx(sc.aggregate()["V0301030105"]["Nickel"],
                                              **APPROX)
    assert not list(g.triples((None, FQ.level, None))), \
        "fq:level is retired — the kind lives only in rdfs:subClassOf"
    unk_nodes = [a for a in g.subjects(FQ.constituent, resolver.FUT[resolver.UNKNOWN_ELEMENT])]
    assert unk_nodes, "no unknownElement constituent served"
    elem = resolver.FUT[resolver.UNKNOWN_ELEMENT]
    assert (elem, resolver.RDFS.subClassOf, resolver.FUT["Element"]) in g


# ===========================================================================
# D. The whole point of the wrapper: the SAME query pattern answers from a
#    pre-materialized node AND from an on-the-fly resolve.
# ===========================================================================

def test_D1_materialized_and_live_agree():
    """Resolving live and reading a pre-materialized fq: graph give identical
    answers for the same (class, element) — the wrapper is transparent."""
    sc = scenarios.ALL["24_multi_class"]
    q = PREFIX + """
        SELECT ?v WHERE { futuram:elvBEV fq:contains ?a .
            ?a fq:constituent futuram:Copper ; fq:amount ?v . }"""
    live = Endpoint(sc)                                  # resolves on demand
    materialized = Endpoint(sc, materialize_all=True)   # pre-computes every node
    v_live = float(one(live.query(q))["v"])
    v_mat  = float(one(materialized.query(q))["v"])
    assert v_live == pytest.approx(v_mat, **APPROX)


# ===========================================================================
# E. Part-of MASS fractions — "how much of component/material X is in Y": structural
#    mass (not element content) via the SAME fq:contains pattern, part = Component/
#    Material class. Oracle: path-product of structural fractions, equal-mean.
# ===========================================================================

def _structural_adj(sc):
    """whole_node -> [(part_node, best_fraction)], step-wise statements only."""
    sc._bind_levels()
    adj = {}
    for s in sc.stmts:
        if not s.levels_skipped:
            adj.setdefault(s.whole, []).append((s.part, s.best_kgkg))
    return adj


def _part_total_in_root(sc, root, level, part_class):
    """Independent path-product of best fractions from root to nodes of
    (level, part_class), stopping at the match. Frozen-oracle-only."""
    adj = _structural_adj(sc)
    total = [0.0]

    def walk(cur, acc):
        for part, frac in adj.get(cur, []):
            f = acc * frac
            if sc.nodes[part].level == level and sc.nodes[part].cls == part_class:
                total[0] += f
            else:
                walk(part, f)
    walk(root, 1.0)
    return total[0]


def _expected_part_in_product(sc, product_class, level, part_class):
    """Equal mean of _part_total_in_root over the product class's
    instances — the independent expectation for part-in-product."""
    sc._bind_levels()
    roots = [r for r in sc._top_instances() if sc.nodes[r].cls == product_class]
    n = len(roots)
    return sum(_part_total_in_root(sc, r, level, part_class)
               for r in roots) / n if n else 0.0


def _ask(ep, whole, part):
    """Query the served amount of `part` (any constituent class) in `whole`."""
    rows = ep.query(PREFIX + f"""
        SELECT ?v WHERE {{ futuram:{whole} fq:contains ?a .
            ?a fq:constituent futuram:{part} ; fq:amount ?v . }}""")
    return float(one(rows)["v"])


def test_E1_component_in_product_matches_oracle():
    """How much of component elvElectricMotor is in product V0301030101 — served
    as the path-product mass fraction (here 0.45, the V01 motor share)."""
    sc = scenarios.ALL["24_multi_class"]
    exp = _expected_part_in_product(sc, "V0301030101_Y2020", "Component", "elvElectricMotor_Y2020")
    assert exp > 1e-9
    assert _ask(Endpoint(sc), "V0301030101_Y2020", "elvElectricMotor_Y2020") == pytest.approx(exp, **APPROX)


def test_E2_component_in_product_served_per_product():
    """The same component class's mass share is served INDEPENDENTLY per product (each
    gets the equal-mean path-product over its OWN instances): V05 = mean(carA, carB) =
    0.45, V01 = carC = 0.30 must NOT coincide (catches one product leaking into another)."""
    sc = scenarios.ALL["24_multi_class"]
    ep = Endpoint(sc)
    v05 = _ask(ep, "V0301030105_Y2020", "elvElectricMotor_Y2020")
    v01 = _ask(ep, "V0301030101_Y2020", "elvElectricMotor_Y2020")
    exp05 = _expected_part_in_product(sc, "V0301030105_Y2020", "Component", "elvElectricMotor_Y2020")
    exp01 = _expected_part_in_product(sc, "V0301030101_Y2020", "Component", "elvElectricMotor_Y2020")
    assert v05 == pytest.approx(exp05, **APPROX)
    assert v01 == pytest.approx(exp01, **APPROX)
    assert v05 != pytest.approx(v01, **APPROX), "per-product shares must stay distinct"


def test_E3_material_in_product_matches_oracle():
    """How much of a MATERIAL class is in a product — same fq:contains pattern,
    path-product over all paths (e.g. highCuAlloys in V0301030105). fq is free to
    flatten the intermediate Component level (one-step-down is a CORE invariant)."""
    sc = scenarios.ALL["24_multi_class"]
    exp = _expected_part_in_product(sc, "V0301030105_Y2020", "Material", "highCuAlloys")
    assert exp > 1e-9
    assert _ask(Endpoint(sc), "V0301030105_Y2020", "highCuAlloys") == pytest.approx(exp, **APPROX)


def test_E3b_explicit_unknown_part_not_double_emitted():
    """Regression: when unknownMaterial is ALREADY a named part of a whole's
    part-of map, the gap-filler must NOT emit a SECOND ~0 unknownMaterial row.
    Exactly one row, carrying the real structural remainder, must be served."""
    from rdflib import Graph, Literal
    from rdflib.namespace import XSD
    from builder.resolver import LEVEL_CLASS, MATERIAL, UNKNOWN_MATERIAL
    from builder.resolver.context import ResolverContext
    from builder.resolver.plugins.partof import _project_whole_part_map
    # seed a composition graph carrying just the year-scoped whole's class_time;
    # the resolver reads class_time from the graph (RDF in -> RDF out).
    comp = Graph()
    comp.add((FUT["V_Y2026"], FUT.referenceYear, Literal(2026, datatype=XSD.int)))
    comp.add((FUT["V_Y2026"], FUT.sliceOf, FUT["V"]))
    comp.add((FUT["V_Y2026"], FUT.sliceAxis, FUT.YearSliceMeanStrategy))
    ctx = ResolverContext(comp)
    # a product whose materials are named PLUS an explicit unknownMaterial catch-all
    # summing to 1.0 (the transformer's shape) — the residual IS that explicit part.
    mapping = {"V_Y2026": {"AHSS": 0.5, "pureCu": 0.379, UNKNOWN_MATERIAL: 0.121}}
    g = Graph()
    _project_whole_part_map(g, ctx, mapping, LEVEL_CLASS[MATERIAL],
                            unknown_class=UNKNOWN_MATERIAL, inferred={}, seen=Graph())
    rows = [float(g.value(a, FQ.amount)) for a in g.subjects(FQ.amount, None)
            if g.value(a, FQ.whole) == FUT["V_Y2026"]
            and g.value(a, FQ.constituent) == FUT[UNKNOWN_MATERIAL]]
    assert len(rows) == 1, f"expected ONE unknownMaterial row, got {rows}"
    assert rows[0] == pytest.approx(0.121, abs=1e-9), \
        "the single row must carry the real structural remainder, not a ~0 ghost"


@pytest.mark.parametrize("sid", ["02_shortfall", "23_product_component",
                                 "25_deep_four_car", "24_multi_class"])
def test_one_unknown_filler_per_parent(sid):
    """GENERAL remainder invariant: EXACTLY ONE unknown<Level> constituent per
    (containing subject, whole, element); the projection emits identical copies that
    _dedup_unknown_constituents collapses to one (distinct scopes stay separate)."""
    from collections import defaultdict
    sc = scenarios.ALL[sid]
    g = Endpoint(sc).served_graph()
    groups = defaultdict(set)
    for subj in g.subjects(FQ.contains, None):
        for a in set(g.objects(subj, FQ.contains)):
            el = g.value(a, FQ.constituent)
            if el is not None and str(el).split("#")[-1].startswith("unknown"):
                groups[(subj, g.value(a, FQ.whole), el)].add(a)
    dups = {(_local_name(k[0]), _local_name(k[2])): len(v)
            for k, v in groups.items() if len(v) > 1}
    assert not dups, f"{sid}: same-parent unknown* duplicates remain: {dups}"


def _local_name(iri):
    return str(iri).split("#")[-1] if iri is not None else None


def test_E4_material_in_component_matches_oracle():
    """How much of a material is in a COMPONENT class (material-in-component),
    equal-mean over the component nodes' instances (e.g. pureCu in
    elvElectricMotor)."""
    sc = scenarios.ALL["24_multi_class"]
    sc._bind_levels()
    # independent expectation: equal-mean over every elvElectricMotor node
    parent = {s.part: s.whole for s in sc.stmts if not s.levels_skipped}

    def top_root(n):
        cur, seen = n, set()
        while cur in parent and cur not in seen:
            seen.add(cur); cur = parent[cur]
        return cur

    motors = [(nm, top_root(nm)) for nm, n in sc.nodes.items()
              if n.level == "Component" and n.cls == "elvElectricMotor_Y2020"]
    cnt = len(motors)
    exp = sum(_part_total_in_root(sc, nm, "Material", "pureCu")
              for nm, r in motors) / cnt
    assert exp > 1e-9
    got = _ask(Endpoint(sc), "elvElectricMotor_Y2020", "pureCu")
    assert got == pytest.approx(exp, **APPROX)

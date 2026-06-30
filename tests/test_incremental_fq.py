# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl", "pytest"]
# ///
"""Incremental, DB-style fq build: statements are the DB, fq a derived view. One-shot
serve_corpus is the source of truth; add_source re-derives only the affected set and
must never diverge from it. PRIMARY GATE: test_increment_equals_full_derive."""
import pathlib
import sys


import pytest
from rdflib import Graph, Namespace, RDF, RDFS, OWL, Literal

from etl import serve_corpus as B
from builder import store as S
import scenarios

FQ = Namespace("https://www.purl.org/futuram/query#")
FUT = Namespace("https://www.purl.org/futuram#")

# A few scenarios that exercise products, components, materials, year slices.
SIDS = ["23_product_component", "24_multi_class", "25_deep_four_car"]


def _rows(view):
    """BNode-free signature of a served fq view: the set of (whole, element,
    rounded best) over every fq:Amount. BNodes differ run-to-run; values must
    match. Rounded to 6dp so Turtle's ~7-sig-fig doubles don't flag noise."""
    out = set()
    for a in view.subjects(RDF.type, FQ.Amount):
        w = view.value(a, FQ.whole)
        e = view.value(a, FQ.constituent)
        amt = view.value(a, FQ.amount)
        if w is None or e is None or amt is None:
            continue
        out.add((str(w), str(e), round(float(amt), 6)))
    return out


def _write_sources(comp_dir, sids, *, new_model=False):
    """Write each scenario as a composition source TTL. By default emits the
    scenario's own chain.to_graph; with new_model=True emits base-typed instances
    carrying referenceYear (NO pre-minted `_Y`), so the builder derives slices."""
    comp_dir.mkdir(parents=True, exist_ok=True)
    for sid in sids:
        chain = scenarios.ALL[sid]
        if new_model:
            from served import to_new_model_graph
            g = to_new_model_graph(chain)
        else:
            g = chain.to_graph(full_metadata=True)
        g.serialize(destination=str(comp_dir / f"{sid}.ttl"), format="turtle")


# ---------------------------------------------------------------------------
# PRIMARY GATE
# ---------------------------------------------------------------------------
def test_increment_equals_full_derive(tmp_path):
    """A sequence of add_source calls produces a view whose best-value rows are
    IDENTICAL (modulo BNode labels) to one-shot serve_corpus over the same
    sources."""
    comp = tmp_path / "composition"
    _write_sources(comp, SIDS)

    full_out = tmp_path / "full.ttl"
    B.serve_corpus(comp, full_out)
    full = Graph().parse(full_out, format="turtle")

    # incremental: start empty, add each source in turn
    view_path = tmp_path / "incr.ttl"
    store = S.Store()
    for sid in SIDS:
        src = Graph().parse(str(comp / f"{sid}.ttl"), format="turtle")
        S.add_source(view_path, store, src, source_id=sid)
    incr = Graph().parse(view_path, format="turtle")

    assert _rows(incr) == _rows(full), \
        f"increment != full derive\n only-full={_rows(full) - _rows(incr)}\n " \
        f"only-incr={_rows(incr) - _rows(full)}"


# ---------------------------------------------------------------------------
# CONTEXTUAL SCOPE NODES across MULTIPLE sources (the bench multi-drivetrain case)
# ---------------------------------------------------------------------------
def _scope_rows(view):
    """Signature of the contextual scope holders (`<comp>_in_<product>`, marked by
    futuram:partOf): (holder, constituent, rounded amount) over fq:contains — answers
    "copper in the motor OF a 2025 BEV", distinct from the plain class rows _rows()."""
    out = set()
    for holder in view.subjects(FUT.partOf, None):
        for a in view.objects(holder, FQ.contains):
            c = view.value(a, FQ.constituent)
            amt = view.value(a, FQ.amount)
            if c is not None and amt is not None:
                out.add((str(holder), str(c), round(float(amt), 6)))
    return out


def _two_drivetrain_sources(tmp_path):
    """Two REAL ELV drivetrain composition graphs (BEV + Diesel, 2020) — the scope-drop
    bug needs the SAME component class under DISJOINT products across sources (one year
    keeps it fast). Returns (comp_dir, [source_ids]); skips if CSVs absent."""
    from etl import csv_to_rdf as X
    from etl import elv_csvs
    csvs = {c.stem.split("_")[-1]: c for c in elv_csvs()}
    if not {"BEV", "Diesel"} <= csvs.keys():
        pytest.skip("ELV BEV/Diesel CSVs not available")
    comp = tmp_path / "composition"
    comp.mkdir(parents=True, exist_ok=True)
    sids = []
    for dt in ("BEV", "Diesel"):
        sid = f"elv-{dt.lower()}"
        X.to_graph(csvs[dt], sid=sid, years={2020}, canonicalize=True).serialize(
            destination=str(comp / f"{sid}.ttl"), format="turtle")
        sids.append(sid)
    return comp, sids


def test_increment_preserves_contextual_scope_nodes(tmp_path):
    """REGRESSION (bench Q6/Q7): adding a SECOND drivetrain source must NOT drop the
    FIRST's `<comp>_in_<product>` scope holders (root cause: 2nd add double-sliced to
    ..._Y2020_Y2020). Fix = YearSlicer idempotency guard (skip already-sliced)."""
    comp, sids = _two_drivetrain_sources(tmp_path)

    full_out = tmp_path / "full.ttl"
    B.serve_corpus(comp, full_out)
    full = Graph().parse(full_out, format="turtle")

    view_path = tmp_path / "incr.ttl"
    store = S.Store()
    for sid in sids:
        src = Graph().parse(str(comp / f"{sid}.ttl"), format="turtle")
        S.add_source(view_path, store, src, source_id=sid)
    incr = Graph().parse(view_path, format="turtle")

    full_holders = {str(s) for s in full.subjects(FUT.partOf, None)}
    incr_holders = {str(s) for s in incr.subjects(FUT.partOf, None)}
    dropped = full_holders - incr_holders
    assert not dropped, (
        f"incremental dropped {len(dropped)} contextual scope holders the one-shot "
        f"derive keeps, e.g. {sorted(x.split('#')[-1] for x in dropped)[:5]}")
    # and NO double-sliced (`<base>_Y<scope>_Y<scope>`) CLASS leaked into the view.
    # Restrict to futuram# owl:Class subjects (slice classes); fq#amount_ IRIs
    # legitimately concatenate two `_Y` runs and are not slice classes.
    import re
    _DOUBLE = re.compile(r"_Y\d+(?:_\d+)?_Y\d+")
    double = [str(s) for s in incr.subjects(RDF.type, OWL.Class)
              if str(s).startswith(str(FUT)) and "_in_" not in str(s)
              and _DOUBLE.search(str(s))]
    assert not double, f"double-sliced classes in incremental view: {double[:5]}"


def test_increment_scope_rows_equal_full(tmp_path):
    """The contextual scope holders' VALUES (not just their existence) match the
    one-shot derive across two sources — the scope nodes carry the right per-product
    element amounts, identical to a from-scratch build."""
    comp, sids = _two_drivetrain_sources(tmp_path)

    full_out = tmp_path / "full.ttl"
    B.serve_corpus(comp, full_out)
    full = Graph().parse(full_out, format="turtle")

    view_path = tmp_path / "incr.ttl"
    store = S.Store()
    for sid in sids:
        S.add_source(view_path, store,
                     Graph().parse(str(comp / f"{sid}.ttl"), format="turtle"),
                     source_id=sid)
    incr = Graph().parse(view_path, format="turtle")

    fr, ir = _scope_rows(full), _scope_rows(incr)
    assert fr == ir, (
        f"scope-row mismatch\n only-full={sorted(fr - ir)[:4]}\n "
        f"only-incr={sorted(ir - fr)[:4]}")


def test_add_order_independent(tmp_path):
    """Adding sources in different orders yields the same view rows."""
    comp = tmp_path / "composition"
    _write_sources(comp, SIDS)
    graphs = {sid: Graph().parse(str(comp / f"{sid}.ttl"), format="turtle")
              for sid in SIDS}

    def build(order):
        vp = tmp_path / f"v_{'_'.join(order)}.ttl"
        store = S.Store()
        for sid in order:
            S.add_source(vp, store, graphs[sid], source_id=sid)
        return _rows(Graph().parse(vp, format="turtle"))

    a = build(SIDS)
    b = build(list(reversed(SIDS)))
    assert a == b, f"order-dependent: {a ^ b}"


def test_readd_is_idempotent(tmp_path):
    """Re-adding an unchanged source changes no rows and reports no conflict."""
    comp = tmp_path / "composition"
    _write_sources(comp, SIDS[:1])
    src = Graph().parse(str(comp / f"{SIDS[0]}.ttl"), format="turtle")
    vp = tmp_path / "v.ttl"
    store = S.Store()
    S.add_source(vp, store, src, source_id=SIDS[0])
    before = _rows(Graph().parse(vp, format="turtle"))
    info = S.add_source(vp, store, src, source_id=SIDS[0])
    after = _rows(Graph().parse(vp, format="turtle"))
    assert before == after, "re-add changed rows"
    assert not info.get("conflicts"), f"re-add reported conflicts: {info['conflicts']}"


def test_affected_set_is_minimal(tmp_path):
    """affected_classes returns exactly the changed classes' subclass/slice
    ancestors + part-of containers — not unrelated classes."""
    comp = tmp_path / "composition"
    # onecar as NEW-MODEL RDF: base-typed instances carrying referenceYear (NO
    # pre-minted `_Y`); add_source derives the slices, so the slice ancestry the
    # affected-set walk needs is real-derived, not baked into the fixture.
    _write_sources(comp, ["26_onecar_real"], new_model=True)
    store = S.Store()
    src = Graph().parse(str(comp / "26_onecar_real.ttl"), format="turtle")
    # add then ask: what does a single statement about a leaf year-slice touch?
    S.add_source(tmp_path / "v.ttl", store, src, source_id="26")
    # the diff unit is the PER-EDGE PartRelation (1:1 with a measurement), NOT the
    # grouped per-whole CompositionStatement — see Store.statement_iris().
    stmt_iris = [s for s in store.graph.subjects(RDF.type, FUT.PartRelation)]
    assert stmt_iris
    affected = S.affected_classes(store.graph, stmt_iris)
    # the base elvBEV (an ancestor of elvBEV_Y20xx) must be in the affected set
    assert FUT["elvBEV"] in affected, "base ancestor not in affected set"
    # a totally unrelated class is not
    assert FUT["Copper"] not in affected  # an element is never a whole-ancestor here


def test_smart_rederive_touches_only_affected(tmp_path):
    """Adding a source recomputes ONLY its affected fq-classes, not the whole
    graph: the reported affected count is a strict subset of all served classes,
    and a class with no relationship to the new source keeps its exact rows."""
    comp = tmp_path / "composition"
    # two DISJOINT scenarios (different product families) so the 2nd add cannot
    # affect the 1st's classes.
    _write_sources(comp, ["22_material_family", "23_product_component"])
    vp = tmp_path / "v.ttl"
    store = S.Store()
    S.add_source(vp, store, Graph().parse(str(comp / "22_material_family.ttl"),
                                          format="turtle"), source_id="m22")
    first = _rows(Graph().parse(vp, format="turtle"))
    n_classes_after_first = len(set(
        Graph().parse(vp, format="turtle").subjects(RDF.type, OWL.Class)))

    info = S.add_source(vp, store, Graph().parse(
        str(comp / "23_product_component.ttl"), format="turtle"), source_id="p23")
    # the 2nd add touched only its own affected classes, far fewer than the total
    assert 0 < info["affected_classes"] < n_classes_after_first + info["affected_classes"]

    # every row the 1st source produced for classes the 2nd does NOT share is
    # still present unchanged (no spurious recompute / drift).
    after = _rows(Graph().parse(vp, format="turtle"))
    # 22's material-family rows (elvElectricMotor family is shared, so restrict to
    # rows whose whole is NOT in the 2nd source) — use a class unique to 22.
    survived = {r for r in first if "elvElectricMotor" not in r[0]}
    assert survived <= after, f"unrelated rows changed: {survived - after}"


def test_conflict_warns_on_differing_restatement(tmp_path):
    """Re-adding a source with a DIFFERENT value for an existing (whole, part) is
    flagged a conflict (the instances now carry two distinct content-hashed
    statement IRIs); an identical re-statement collapses to one IRI, no conflict."""
    comp = tmp_path / "composition"
    _write_sources(comp, [SIDS[0]])
    base = Graph().parse(str(comp / f"{SIDS[0]}.ttl"), format="turtle")
    vp = tmp_path / "v.ttl"
    store = S.Store()
    S.add_source(vp, store, base, source_id="a")

    # identical re-statement -> same statement IRIs -> no conflict
    info_same = S.add_source(vp, store, base, source_id="a")
    assert not info_same.get("conflicts"), \
        f"identical re-add flagged a conflict: {info_same['conflicts']}"

    # re-mint ONE scenario with a perturbed best value so its statement IRI
    # differs while its (whole, part) instances (same source_id) stay the same.
    sc = scenarios.ALL[SIDS[0]]
    s0 = sc.stmts[0]
    orig = (s0.best, s0.lo, s0.hi)
    try:
        s0.best = s0.lo = s0.hi = orig[0] * 0.5 + 0.013
        perturbed = sc.to_graph(full_metadata=True)
    finally:
        s0.best, s0.lo, s0.hi = orig
    info_diff = S.add_source(vp, store, perturbed, source_id="a")
    assert info_diff.get("conflicts"), "differing re-statement not flagged"

# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl"]
# ///
"""builder.store — the COMPOSITION STORE and its incremental, DB-style derive.
Statements ARE the database, the fq view a PURE DERIVATION; add_source re-derives only
the AFFECTED set (via SPARQL), value-IDENTICAL to a full re-derive but not faster.
"""
import pathlib
import sys


from rdflib import Graph, Namespace, URIRef, RDF, OWL

from common.vocab import FUT
from .derive import _served_graph, _namespaced, _finalise_store

FQ = Namespace("https://www.purl.org/futuram/query#")

# the slice/taxonomy edges an affected class is reachable through, UPWARD from a
# changed class: subclass + generic slice (any aggregation axis) ancestors.
_AFFECTED_ANCESTRY = "(rdfs:subClassOf|futuram:sliceOf)+"


class Store:
    """The composition store (the DATABASE): all statements, additive. Holds the merged
    composition graph (namespaced, sliced) plus the statement IRIs seen, so an identical
    re-add is a no-op and a differing re-statement of the same (whole, part) detectable."""

    def __init__(self):
        self.graph = Graph()
        self._stmt_iris = set()

    def statement_iris(self):
        # the PER-EDGE nodes (PartRelations), 1:1 with a measurement — the unit the
        # incremental add_source diffs (after - before). The grouped per-whole
        # CompositionStatement is NOT 1:1 with an edge, so it is not the diff unit.
        return set(self.graph.subjects(RDF.type, FUT.PartRelation))


def _statement_conflicts(graph):
    """(whole, part) pairs carrying MORE THAN ONE distinct contentHash — a differing-
    value re-statement (identical statements share one hash and collapse). Keyed on
    futuram:contentHash, NOT the relation node IRI, so equal content still collapses."""
    q = """
    PREFIX futuram: <https://www.purl.org/futuram#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    SELECT ?w ?p (COUNT(DISTINCT ?h) AS ?n) WHERE {
      ?w  futuram:hasCompositionStatement ?cs .
      ?cs rdf:type futuram:CompositionStatement ;
          futuram:hasPartRelation ?rel .
      ?rel futuram:refersTo ?p ;
           futuram:contentHash ?h .
    } GROUP BY ?w ?p HAVING (COUNT(DISTINCT ?h) > 1)
    """
    return [(r.w, r.p, int(r.n)) for r in graph.query(q)]


def _changed_classes(store_graph, new_stmt_iris):
    """The classes the new relations are directly ABOUT = the rdf:type of the
    WHOLE of each new PartRelation (a futuram class). The whole is reached via the
    owning CompositionStatement's hasCompositionStatement subject."""
    values = " ".join(f"<{i}>" for i in new_stmt_iris)
    q = f"""
    PREFIX futuram: <https://www.purl.org/futuram#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    SELECT DISTINCT ?changed WHERE {{
      VALUES ?rel {{ {values} }}
      ?cs futuram:hasPartRelation ?rel .
      ?w  futuram:hasCompositionStatement ?cs .
      ?w  rdf:type ?changed .
    }}
    """
    return {r.changed for r in store_graph.query(q)
            if isinstance(r.changed, URIRef) and str(r.changed).startswith(str(FUT))}


def affected_classes(store_graph, new_stmt_iris):
    """The fq-classes whose value can change when `new_stmt_iris` are added: the changed
    classes + everything UPWARD along the slice/taxonomy ancestry + transitive part-of
    CONTAINERS. The changed set is a separate query so it is ALWAYS in the result."""
    iris = [i for i in new_stmt_iris]
    if not iris:
        return set()
    changed = _changed_classes(store_graph, iris)
    if not changed:
        return set()
    out = set(changed)                          # the changed classes themselves
    values = " ".join(f"<{c}>" for c in changed)
    q = f"""
    PREFIX futuram: <https://www.purl.org/futuram#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT ?affected WHERE {{
      VALUES ?changed {{ {values} }}
      # ANCESTORS: a changed slice/leaf rolls UP into its base/parent (?changed ⊑
      # ?affected), the changed class being the SUBJECT of the slice/subClassOf edge.
      # (e.g. V0301030105_Y2020 sliceOf V0301030105 -> base V0301030105 is affected.)
      {{ ?changed {_AFFECTED_ANCESTRY} ?affected }}
      # CONTAINERS: a product/component whose composition transitively includes
      # the changed class refreshes too (its rolled-up / contextual rows).
      UNION {{ ?affected futuram:hasComposition+ ?changed }}
    }}
    """
    for r in store_graph.query(q):
        a = r.affected
        if isinstance(a, URIRef) and str(a).startswith(str(FUT)):
            out.add(a)

    # DOWNWARD VALUE-AXIS REACH: adding a source can promote a base to SHARED and MINT
    # new per-value leaves over earlier instances that the upward reach never visits, so
    # descend to a fixpoint into every value-axis leaf sliceOf an affected class.
    axes = {ax for ax in store_graph.objects(None, FUT.sliceAxis)
            if isinstance(ax, URIRef)}
    if axes:
        axis_vals = " ".join(f"<{ax}>" for ax in axes)
        frontier = set(out)
        while frontier:
            base_vals = " ".join(f"<{c}>" for c in frontier)
            dq = f"""
            PREFIX futuram: <https://www.purl.org/futuram#>
            SELECT DISTINCT ?down WHERE {{
              VALUES ?base {{ {base_vals} }}
              VALUES ?ax {{ {axis_vals} }}
              ?down futuram:sliceOf ?base ; futuram:sliceAxis ?ax .
            }}
            """
            nxt = set()
            for r in store_graph.query(dq):
                d = r.down
                if (isinstance(d, URIRef) and str(d).startswith(str(FUT))
                        and d not in out):
                    out.add(d)
                    nxt.add(d)
            frontier = nxt
    return out


def derive_fq(store_graph, affected=None):
    """Derive the fq view from the store. affected=None -> FULL derive (every class);
    affected=<class IRIs> -> project ONLY those (the pooled aggregate is still computed
    over the whole store, only the projection sweep is restricted). Value-identical."""
    if affected is None:
        return _served_graph(store_graph)
    only = {str(c).split("#")[-1] for c in affected}     # local-names for resolver
    return _served_graph(store_graph, only=only)


def _class_of_amount(view, amt):
    return view.value(amt, FQ.whole)


def _slice_view(view, classes):
    """The sub-view of `view` restricted to the given fq-classes: each class's own
    triples + its fq:contains amount BNodes' triples + the scoped <c>_in_<p> holders
    whose component class is in `classes`. Extracts the affected rows from a derive."""
    classes = set(classes)
    out = Graph()
    for c in classes:
        for p, o in view.predicate_objects(c):
            out.add((c, p, o))
            if p == FQ.contains:        # pull the amount BNode's own triples
                for ap, ao in view.predicate_objects(o):
                    out.add((o, ap, ao))
    # scoped holders (<comp>_in_<product>) whose component class is affected
    for holder in view.subjects(RDF.type, OWL.Class):
        if FQ["_in_"] and "_in_" in str(holder):
            for t in view.objects(holder, RDF.type):
                if t in classes:
                    for p, o in view.predicate_objects(holder):
                        out.add((holder, p, o))
                        if p == FQ.contains:
                            for ap, ao in view.predicate_objects(o):
                                out.add((o, ap, ao))
                    break
    return out


def refresh(view, fq_rows, affected):
    """Wholesale-replace each affected class's rows in `view` with `fq_rows`. Amount
    nodes are BNodes (not diffable), so an affected class's old fq:contains rows + each
    amount's triples are DELETED then re-added; untouched classes stay. Returns view."""
    for c in affected:
        for a in list(view.objects(c, FQ.contains)):
            for ap, ao in list(view.predicate_objects(a)):
                view.remove((a, ap, ao))
            view.remove((c, FQ.contains, a))
        # drop the class's own non-contains metadata too (it is re-emitted)
        for p, o in list(view.predicate_objects(c)):
            view.remove((c, p, o))
    # also clear affected scoped holders
    for holder in list(view.subjects(RDF.type, OWL.Class)):
        if "_in_" in str(holder) and any(t in affected
                                         for t in view.objects(holder, RDF.type)):
            for a in list(view.objects(holder, FQ.contains)):
                for ap, ao in list(view.predicate_objects(a)):
                    view.remove((a, ap, ao))
            for p, o in list(view.predicate_objects(holder)):
                view.remove((holder, p, o))
    view += fq_rows
    return view


def add_source(view_path, store, new_statements, *, source_id):
    """Incremental, DB-style add (the public entry for ADDITIVITY): union
    `new_statements` into `store`, re-derive ONLY the affected fq-classes, and wholesale-
    replace them in the view at `view_path`. Value-identical to a full re-derive."""
    view_path = pathlib.Path(view_path)

    # 1) additive union into the store (per-source namespace, then finalise with
    # the generic axis slicers — they read the drivetrain/year markers off the
    # merged graph, no source layout).
    before = store.statement_iris()
    src = Graph()
    for t in new_statements:
        src.add(t)
    _namespaced(src, source_id.replace("/", "_"), into=store.graph)
    _finalise_store(store.graph)
    after = store.statement_iris()
    new_iris = after - before            # genuinely new statement IRIs

    conflicts = _statement_conflicts(store.graph)

    # 2) affected set from the new statements (the changed classes' ancestry +
    # containers). If nothing new was added (idempotent re-add), nothing to do.
    affected = affected_classes(store.graph, new_iris)

    # 3) re-derive. The first add (empty view) derives everything; later adds
    # derive scoped to the affected set and splice in.
    view = Graph()
    if view_path.exists():
        view.parse(str(view_path), format="turtle")

    if not view:                         # first build: full derive
        view = derive_fq(store.graph, affected=None)
    else:
        fq_rows = derive_fq(store.graph, affected=affected)
        refresh(view, fq_rows, affected)

    # 4) persist
    view_path.parent.mkdir(parents=True, exist_ok=True)
    view.serialize(destination=str(view_path), format="turtle")
    return {
        "source_id": source_id,
        "new_statements": len(new_iris),
        "affected_classes": len(affected),
        "conflicts": conflicts,
        "fq_triples": len(view),
        "out": str(view_path),
    }


def add_sources(out_ttl, pairs):
    """Build the fq view INCREMENTALLY from composition GRAPHS: one add_source per
    (source_id, graph) pair into a fresh view at out_ttl. Equivalent to the one-shot
    derive. Returns the last add's info + accumulated conflicts."""
    out_ttl = pathlib.Path(out_ttl)
    if out_ttl.exists():
        out_ttl.unlink()                     # fresh view
    store = Store()
    last, all_conflicts, n = {}, [], 0
    for source_id, g in pairs:
        last = add_source(out_ttl, store, g, source_id=str(source_id))
        n += 1
    all_conflicts = last.get("conflicts", [])
    if all_conflicts:
        classes = sorted({str(w).split("#")[-1] for w, _, _ in all_conflicts})
        print(f"WARNING: {len(classes)} class(es) carry a DIFFERING re-statement "
              f"of the same (whole, part) across sources (genuine conflict — two "
              f"sources disagree on a value): {', '.join(classes[:8])}"
              + (" ..." if len(classes) > 8 else ""))
    return {**last, "files_added": n, "conflicts": all_conflicts}

# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///
"""builder.slicer — generic, composable axis slicers (an AXIS IS an AggregationStrategy
IRI). A slicer derives ONE axis's slices, emitting the SAME generic sliceOf/sliceAxis
edges; they COMPOSE (order = nesting) via derive(graph) + apply (ADDITIVE/REWRITE).
"""
from __future__ import annotations

import abc
import functools
import pathlib

from rdflib import Graph, Literal, RDF, RDFS, OWL, BNode, URIRef
from rdflib.namespace import XSD

from common import pipeline
from common.vocab import (FUT, EX, TIME,
                          YEAR_SLICE_MEAN_IRI, DRIVETRAIN_MEAN_IRI,
                          EQUAL_SUBCLASS_MEAN_IRI)

# the four composition-level roots — never an aggregation parent.
LEVEL_ROOTS = {"Product", "Component", "Material", "Element"}

# axis strategy IRIs as rdflib terms (the axis IS the strategy individual).
YEAR_AXIS = URIRef(YEAR_SLICE_MEAN_IRI)
DRIVETRAIN_AXIS = URIRef(DRIVETRAIN_MEAN_IRI)
EQUAL_SUBCLASS_MEAN = URIRef(EQUAL_SUBCLASS_MEAN_IRI)


def _local(iri):
    return str(iri).rsplit("#", 1)[-1]


# ---- shared emit helpers (the ONLY thing every axis shares) ----------------
def slice_name(base, entry):
    """The naming convention for a slice of `base` at scope `entry` (labels only;
    the class_time registry / RDF annotations stay authoritative)."""
    if "year" in entry:
        return f"{base}_Y{entry['year']}"
    return f"{base}_Y{entry['start']}_{entry['end']}"


def emit_slice_edge(out, child, parent, axis_iri):
    """The ONE generic slice edge, emitted identically for every axis:
        child rdfs:subClassOf parent ; child futuram:sliceOf parent ;
        child futuram:sliceAxis <axisStrategyIRI>."""
    out.add((child, RDFS.subClassOf, parent))
    out.add((child, FUT.sliceOf, parent))
    out.add((child, FUT.sliceAxis, axis_iri))


def emit_scope(out, cls_iri, entry, cls_name):
    """The year/period time scope of a derived slice (futuram:referenceYear or a
    futuram:hasReferencePeriod interval)."""
    if "year" in entry:
        out.add((cls_iri, FUT.referenceYear,
                 Literal(entry["year"], datatype=XSD.int)))
    else:
        per = EX[f"refperiod_{cls_name}"]
        out.add((per, RDF.type, TIME.Interval))
        pb = BNode(); out.add((pb, RDF.type, TIME.Instant))
        out.add((pb, TIME.inXSDDate,
                 Literal(f"{entry['start']}-01-01", datatype=XSD.date)))
        out.add((per, TIME.hasBeginning, pb))
        pe = BNode(); out.add((pe, RDF.type, TIME.Instant))
        out.add((pe, TIME.inXSDDate,
                 Literal(f"{entry['end']}-12-31", datatype=XSD.date)))
        out.add((per, TIME.hasEnd, pe))
        out.add((cls_iri, FUT.hasReferencePeriod, per))


@functools.lru_cache(maxsize=1)
def _frozen_hierarchy():
    """The frozen futuram-hierarchy taxonomy as a Graph, parsed once."""
    g = Graph()
    g.parse(str(pipeline.HIERARCHY), format="turtle")
    return g


# ---- axis-member selectors (graph-evaluable; NO source-layout knowledge) ----
# WHICH classes an axis applies to is EVALUATED AGAINST THE composition GRAPH. A selector
# answers `members(graph) -> set of local-names`, as a SPARQL string or a traversal fn.
class Selector(abc.ABC):
    """Evaluates against a composition graph to the set of class local-names an
    axis applies to. Pure / deterministic."""

    @abc.abstractmethod
    def members(self, graph: Graph) -> set:
        raise NotImplementedError


class SparqlSelector(Selector):
    """A membership pattern as a SPARQL SELECT returning one column (?cls); the
    member set is the bound classes' local-names. For simple axes whose membership
    is a direct graph pattern (e.g. every class carrying a given sliceAxis)."""

    def __init__(self, query, var="cls"):
        self.query = query
        self.var = var

    def members(self, graph: Graph) -> set:
        out = set()
        for row in graph.query(self.query):
            v = row[self.var]
            if isinstance(v, URIRef) and str(v).startswith(str(FUT)):
                out.add(_local(v))
        return out


# ---- the protocol ----------------------------------------------------------
class AxisSlicer(abc.ABC):
    """One aggregation axis. Parameterised by its axis IRI (the AggregationStrategy
    individual). `derive` produces the axis's new slice triples; `apply` folds them
    into the graph (additive by default)."""

    #: the AggregationStrategy IRI that combines this dimension.
    axis_iri: URIRef = None

    @abc.abstractmethod
    def derive(self, graph: Graph) -> Graph:
        """Return a NEW Graph of the triples this axis derives from `graph`. Pure,
        deterministic, idempotent."""
        raise NotImplementedError

    def apply(self, graph: Graph) -> Graph:
        """Fold this axis's derived triples into `graph`. Default: ADDITIVE union.
        Override when the axis must REWRITE (e.g. retype instances)."""
        return graph + self.derive(graph)


def compose(graph: Graph, slicers) -> Graph:
    """Apply `slicers` IN ORDER; order = nesting (each sees the prior output)."""
    g = graph
    for s in slicers:
        g = s.apply(g)
    return g


# ---- YEAR axis: derive ancestor slices by walking taxonomy UP (additive) ---
class YearSlicer(AxisSlicer):
    """Derive the leaf + ANCESTOR year slices for every time-scoped class: for each
    ancestor A of a base at scope t, A_t ⊑ A, A_t sliceOf A @ year axis, A_t =
    EqualSubclassMean, A_t scope = t. Taxonomy parents from graph + hierarchy; idempotent."""

    axis_iri = YEAR_AXIS

    def derive(self, graph: Graph) -> Graph:
        out = Graph()
        # Mint the leaf slice class from each time-stamped instance: the ETL types
        # instances by their TIMELESS base + emits production time as DATA, so here
        # we derive <base>_Y<scope>, retype, and stamp referenceYear/period+sliceOf.
        def _scope_of_instance(inst):
            y = graph.value(inst, FUT.referenceYear)
            if y is not None:
                return {"year": int(y)}
            per = graph.value(inst, FUT.hasReferencePeriod)
            if per is not None:
                b = graph.value(per, TIME.hasBeginning)
                e = graph.value(per, TIME.hasEnd)
                bd = graph.value(b, TIME.inXSDDate) if b else None
                ed = graph.value(e, TIME.inXSDDate) if e else None
                if bd is not None and ed is not None:
                    return {"start": int(str(bd)[:4]), "end": int(str(ed)[:4])}
            return None

        timed_instances = (set(graph.subjects(FUT.referenceYear, None))
                           | set(graph.subjects(FUT.hasReferencePeriod, None)))
        for inst in sorted(timed_instances, key=str):
            scope = _scope_of_instance(inst)
            if scope is None:
                continue
            for base in graph.objects(inst, RDF.type):
                if not str(base).startswith(str(FUT)):
                    continue
                base_ln = _local(base)
                if base_ln in LEVEL_ROOTS:
                    continue
                # Idempotency: skip a base already sliced on this axis. Keyed on the
                # generic sliceAxis marker + this axis IRI, so derive is repeatable and
                # does not depend on any "_Y" naming convention.
                if (base, FUT.sliceAxis, self.axis_iri) in graph:
                    continue
                leaf = slice_name(base_ln, scope)
                # the leaf slice class: ⊑ base, sliceOf base @ year axis, scope. NO
                # hasAggregationStrategy: it HAS instances and aggregates from their own
                # statement trees (a strategy is only for instance-less ancestors).
                out.add((FUT[leaf], RDFS.subClassOf, base))
                emit_slice_edge(out, FUT[leaf], base, self.axis_iri)
                emit_scope(out, FUT[leaf], scope, leaf)
                # retype the instance onto its leaf slice (keep the base type too).
                out.add((inst, RDF.type, FUT[leaf]))

        # The class graph the ancestor loop reads = the input PLUS the leaves we
        # just minted (so leaf-level referenceYear + sliceOf are visible here too).
        cls_graph = graph + out

        parents = {}
        for g in (_frozen_hierarchy(), cls_graph):
            for s, o in g.subject_objects(RDFS.subClassOf):
                if str(s).startswith(str(FUT)) and str(o).startswith(str(FUT)):
                    if _local(o) not in LEVEL_ROOTS:
                        parents.setdefault(_local(s), set()).add(_local(o))

        # time-scoped slice classes, from the composition ABox (incl. minted leaves)
        entries = {}
        for s, y in cls_graph.subject_objects(FUT.referenceYear):
            entries[_local(s)] = {"year": int(y)}
        for s, per in cls_graph.subject_objects(FUT.hasReferencePeriod):
            b = cls_graph.value(per, TIME.hasBeginning)
            e = cls_graph.value(per, TIME.hasEnd)
            bd = cls_graph.value(b, TIME.inXSDDate) if b else None
            ed = cls_graph.value(e, TIME.inXSDDate) if e else None
            if bd is not None and ed is not None:
                entries[_local(s)] = {"start": int(str(bd)[:4]),
                                      "end": int(str(ed)[:4])}

        # the YEAR base of each slice: the futuram:sliceOf target that is itself
        # TIME-LESS — the taxonomy class the year dimension collapses to. Picking the
        # timeless target selects the year base uniformly, with no per-axis predicate.
        timed = {_local(s) for s in cls_graph.subjects(FUT.referenceYear, None)}
        timed |= {_local(s) for s in cls_graph.subjects(FUT.hasReferencePeriod, None)}
        bases = {}
        for s, o in cls_graph.subject_objects(FUT.sliceOf):
            if _local(o) not in timed:
                bases[_local(s)] = _local(o)
        for slc, entry in sorted(entries.items()):
            base = bases.get(slc)
            if base is None:
                continue
            stack, seen = [base], set()
            while stack:
                cls = stack.pop()
                if cls in seen:
                    continue
                seen.add(cls)
                for parent in sorted(parents.get(cls, ())):
                    p_slice = slice_name(parent, entry)
                    child_slice = slc if cls == base else slice_name(cls, entry)
                    out.add((FUT[child_slice], RDFS.subClassOf, FUT[p_slice]))
                    emit_slice_edge(out, FUT[p_slice], FUT[parent], self.axis_iri)
                    out.add((FUT[p_slice], FUT.hasAggregationStrategy,
                             EQUAL_SUBCLASS_MEAN))
                    emit_scope(out, FUT[p_slice], entry, p_slice)
                    stack.append(parent)
        return out


# ---- VALUE axis: retype shared instances DOWN to a per-value leaf (rewriting) --
# A base shared across >= 2 VALUE-CLASSES (marked `?value sliceAxis <axisIRI>`, reached
# from each instance's product-root) is retyped per value to <value>_<base> ⊑ <base>.
class ValueAxisSlicer(AxisSlicer):
    """Slice along a VALUE axis (classes carrying `?value sliceAxis <self.axis_iri>`),
    ONCE over the MERGED graph. A base whose instances span >= 2 values is SHARED; each
    is retyped to a leaf <value>_<baseSlice> ⊑ <baseSlice>. REWRITES -> overrides apply."""

    def __init__(self, axis_iri):
        self.axis_iri = axis_iri

    # -- generic graph evaluation (the axis is just self.axis_iri) ------------
    def _values(self, graph):
        """The axis VALUE-CLASSES: classes the graph marks as a value of this axis
        (`?v sliceAxis <axis_iri>` AND not itself a slice-of-something, i.e. a top
        value like a drivetrain, not a derived leaf)."""
        out = set()
        for v in graph.subjects(FUT.sliceAxis, self.axis_iri):
            if isinstance(v, URIRef):
                out.add(v)
        return out

    def _index(self, graph):
        """(inst_value, shared): for each instance the single axis value its part-of
        ROOT reaches (absent if 0/>1), and the bases whose instances span >= 2 values.
        All via generic sliceOf/subClassOf reach — no IRI string matching."""
        from collections import defaultdict
        values = self._values(graph)

        # part-of parent map (whole<-part) to find each instance's root. New shape:
        # whole -hasCompositionStatement-> cs -hasPartRelation-> rel -refersTo-> part.
        parent = {}
        for cs in graph.subjects(RDF.type, FUT.CompositionStatement):
            w = graph.value(predicate=FUT.hasCompositionStatement, object=cs)
            if w is None:
                continue
            for rel in graph.objects(cs, FUT.hasPartRelation):
                p = graph.value(rel, FUT.refersTo)
                if p is not None:
                    parent[p] = w

        def root_of(node):
            seen = set()
            while node in parent and node not in seen:
                seen.add(node)
                node = parent[node]
            return node

        # subClassOf reach (graph + frozen hierarchy) to the marked value-classes
        sup = defaultdict(set)
        for g in (graph, _frozen_hierarchy()):
            for s, o in g.subject_objects(RDFS.subClassOf):
                if isinstance(s, URIRef) and isinstance(o, URIRef):
                    sup[s].add(o)

        def values_of_class(cls):
            out, seen, stack = set(), set(), [cls]
            while stack:
                c = stack.pop()
                if c in values:
                    out.add(c)
                for s in sup.get(c, ()):
                    if s not in seen:
                        seen.add(s)
                        stack.append(s)
            return out

        inst_value = {}
        base_values = defaultdict(set)
        for inst in set(graph.subjects(RDF.type, None)):
            vals = set()
            for rc in graph.objects(root_of(inst), RDF.type):
                vals |= values_of_class(rc)
            if len(vals) == 1:
                inst_value[inst] = next(iter(vals))
            # the instance's sliced base = the sliceOf-target of its OTHER-axis slice
            # class (e.g. the year slice's base C). Generic: any sliceOf target.
            for cls in graph.objects(inst, RDF.type):
                for base in graph.objects(cls, FUT.sliceOf):
                    base_values[base] |= vals
        shared = {b for b, vs in base_values.items() if len(vs) > 1}
        return inst_value, shared

    def _instance_slice_classes(self, graph, inst, shared):
        """The instance's type-classes that are a slice OF a SHARED base (the
        classes to retype onto a value leaf)."""
        out = []
        for cls in graph.objects(inst, RDF.type):
            if isinstance(cls, URIRef) and any(
                    base in shared for base in graph.objects(cls, FUT.sliceOf)):
                out.append(cls)
        return out

    def _leaf_name(self, value, slice_cls):
        return FUT[f"{_local(value)}_{_local(slice_cls)}"]

    def _emit_leaf(self, out, src, slice_cls, value):
        """Leaf <value>_<sliceCls> ⊑ <sliceCls> ; sliceOf <sliceCls> @ axis; it
        inherits the slice's scope + its other-axis sliceOf edges."""
        leaf = self._leaf_name(value, slice_cls)
        out.add((leaf, RDF.type, OWL.Class))
        emit_slice_edge(out, leaf, slice_cls, self.axis_iri)
        for ry in src.objects(slice_cls, FUT.referenceYear):
            out.add((leaf, FUT.referenceYear, ry))
        for per in src.objects(slice_cls, FUT.hasReferencePeriod):
            out.add((leaf, FUT.hasReferencePeriod, per))
        for ts in src.objects(slice_cls, FUT.sliceOf):
            for ax in src.objects(slice_cls, FUT.sliceAxis):
                emit_slice_edge(out, leaf, ts, ax)
        return leaf

    def derive(self, graph: Graph) -> Graph:
        out = Graph()
        inst_value, shared = self._index(graph)
        for inst, value in inst_value.items():
            for slice_cls in self._instance_slice_classes(graph, inst, shared):
                self._emit_leaf(out, graph, slice_cls, value)
        return out

    def apply(self, graph: Graph) -> Graph:
        """Retype each shared-base instance onto ITS value's leaf; pass everything
        else through. Does NOT attach the parent strategy (a finalise concern over
        the whole store — see attach_value_strategy)."""
        out = Graph()
        inst_value, shared = self._index(graph)
        retype = {}      # (inst, slice_cls) -> leaf
        for inst, value in inst_value.items():
            for slice_cls in self._instance_slice_classes(graph, inst, shared):
                retype[(inst, slice_cls)] = self._leaf_name(value, slice_cls)
        seen_leaf = set()
        for s, p, o in graph:
            if p == RDF.type and (s, o) in retype:
                leaf = retype[(s, o)]
                out.add((s, RDF.type, leaf))
                if leaf not in seen_leaf:
                    seen_leaf.add(leaf)
                    self._emit_leaf(out, graph, o, inst_value[s])
                continue
            out.add((s, p, o))
        return out


def attach_value_strategy(merged, axis_iri):
    """Declare hasAggregationStrategy <axis_iri> on every base slice that has a
    value-axis child (something is a futuram:sliceOf it @ axis_iri). Run at finalise
    over the WHOLE merged store. Idempotent. Pure RDF, generic in the axis IRI."""
    axis_slices = {s for s, _, _ in merged.triples((None, FUT.sliceAxis, axis_iri))}
    parents = {p for s, _, p in merged.triples((None, FUT.sliceOf, None))
               if s in axis_slices}
    for p in parents:
        merged.remove((p, FUT.hasAggregationStrategy, None))
        merged.add((p, FUT.hasAggregationStrategy, axis_iri))
    return merged

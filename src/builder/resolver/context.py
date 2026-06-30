"""resolver.context — the shared, compute-once state a projection run owns. Seeded with
the INPUT COMPOSITION GRAPH; everything a plugin reads is a PURE FUNCTION of it
(cached_property), so an EMPTY ctx recreates it all identically. A CACHE, never truth.
"""
from __future__ import annotations

import functools
from collections import defaultdict

from rdflib import Graph, RDFS

from common import pipeline
from common.vocab import LEVELS

from . import vocab as V
from .vocab import FUT, COMPONENT, MATERIAL, PRODUCT

from builder.index import build_index, ancestors_of as _idx_ancestors
from builder import aggregate as A


@functools.lru_cache(maxsize=1)
def _default_tbox():
    """The standard TBox (futuram-hierarchy + composition-statement), parsed once;
    used only when a caller does not inject one."""
    g = Graph()
    g.parse(pipeline.HIERARCHY, format="turtle")
    g.parse(pipeline.TBOX, format="turtle")
    g.parse(pipeline.UNCERTAINTY_TBOX, format="turtle")   # ruleset + dqvAggregationRule
    return g


def _propagation_compounds(tbox):
    """Read the UncertaintyRuleset's tree-propagation discipline (pathProductCompounds)
    off the TBox: True -> multiplying edges compound, False -> leaf-only RSS, None when
    no ruleset / no facet (uncertainty then a no-op). Dispatch is on RDF, not hardcoded."""
    from rdflib import RDF
    rulesets = list(tbox.subjects(RDF.type, FUT.UncertaintyRuleset))
    if not rulesets:
        return None
    prop = tbox.value(rulesets[0], FUT.uncertaintyPropagation)
    if prop is None:
        return None
    flag = tbox.value(prop, FUT.pathProductCompounds)
    return bool(flag) if flag is not None else False


def _as_graph(source):
    """Normalise the run input to a composition rdflib.Graph: a Graph directly, or any
    object with `.to_graph()`. The GRAPH is the sole source of truth from here on."""
    if isinstance(source, Graph):
        return source
    to_graph = getattr(source, "to_graph", None)
    if to_graph is not None:
        return to_graph()
    raise TypeError(
        f"ResolverContext needs a composition rdflib.Graph (or an object with "
        f".to_graph()); got {type(source).__name__}")


class ResolverContext:
    """All state for one projection run. Build once, hand to every plugin. Seeded
    with the INPUT COMPOSITION GRAPH; every read is a pure function of it."""

    def __init__(self, source, *, graph=None, only=None, tbox=None):
        self.graph_in = _as_graph(source)
        self._sid = getattr(source, "id", None)
        self.graph = graph if graph is not None else Graph()
        self.want = None if only is None else set(only)
        self.tbox = tbox if tbox is not None else _default_tbox()
        self._cache: dict = {}

    # ---- the index: the single derived structure everything reads ------------
    @functools.cached_property
    def index(self):
        """CompositionIndex built FROM THE INPUT GRAPH — the pure-function root of
        every other derived attribute. Rebuilt on a fresh ctx."""
        return build_index(self.graph_in, sid=self._sid)

    # ---- composition reads over the index (the plugins call these) -----------
    @property
    def class_time(self):
        return self.index.class_time

    def node_class(self, name):
        return self.index.classes.get(name)

    def node_level(self, name):
        return self.index.levels.get(name)

    def classes_at_level(self, level):
        """Sorted futuram classes that have at least one node at `level`."""
        return sorted({cls for nm, cls in self.index.classes.items()
                       if self.index.levels[nm] == level})

    def top_instances(self):
        return A.top_instances(self.index)

    def element_in_whole(self, whole, element_cls, use="best"):
        return A.element_in_whole(self.index, whole, element_cls, use)

    @functools.cached_property
    def _unc_compounds(self):
        """The ruleset's tree-propagation discipline (pathProductCompounds), or None
        when no ruleset is present (uncertainty is then not served)."""
        return _propagation_compounds(self.tbox)

    def element_uncertainty_over_nodes(self, nodes, element_cls):
        """Relative uncertainty of the equal-mean amount of `element_cls` over `nodes`
        — the uncertainty twin of element_in_whole, over the SAME node set. None when
        no ruleset/content. Stamped as the amount's fq:relativeUncertainty."""
        if self._unc_compounds is None:
            return None
        return A.element_uncertainty_over_nodes(
            self.index, nodes, element_cls,
            compound_products=self._unc_compounds)

    def element_uncertainty_over_nodes_weighted(self, nodes, node_weights, element_cls):
        """Relative uncertainty of the MASS-WEIGHTED amount of `element_cls` over
        `nodes` — use when the value was computed as a mass-weighted mean (e.g. group
        scopes). `node_weights` is a {node_name: mass} dict."""
        if self._unc_compounds is None:
            return None
        return A.element_uncertainty_over_nodes_weighted(
            self.index, nodes, node_weights, element_cls,
            compound_products=self._unc_compounds)

    def element_uncertainty_in_class(self, cls_name, element_cls):
        """Relative uncertainty for a derived CLASS amount (product/component class),
        from the precomputed class-level reach aggregate. None when absent."""
        return self.agg_uncertainty.get(cls_name, {}).get(element_cls)

    def element_dq_over_nodes(self, nodes, element_cls):
        """(mean_dq, dqs) of the equal-mean amount of `element_cls` over `nodes` — the
        DQ twin of element_uncertainty_over_nodes, over the SAME node set. None when no
        ruleset/DQ content. Stamped as the amount's fq:meanDataQuality + fq:dqs."""
        if self._unc_compounds is None:
            return None
        return A.element_dq_over_nodes(self.index, nodes, element_cls)

    def element_dq_in_class(self, cls_name, element_cls):
        """(mean_dq, dqs) for a derived CLASS amount (product/component class), from
        the precomputed class-level DQ aggregate. None when absent."""
        return self.agg_dq.get(cls_name, {}).get(element_cls)

    def parent_gate(self, parent, fam_pc, candidates):
        return A.parent_gate(self.index, parent, fam_pc, candidates)

    def unknowns(self):
        return A.unknowns(self.index)

    # ---- hierarchy -----------------------------------------------------------
    @functools.cached_property
    def superclasses(self):
        """class local-name -> set of direct futuram superclass local-names, as the
        index assembled it from the frozen hierarchy + the graph's own subClassOf
        edges."""
        return self.index.superclasses

    # the four composition-level roots — too generic to aggregate over
    _LEVEL_ROOTS = frozenset({"Product", "Component", "Material", "Element"})

    def ancestors_of(self, cls_name, include_self=True):
        """Transitive futuram superclasses of `cls_name` (minus the level roots),
        over the index's superclass map."""
        return _idx_ancestors(self.index, cls_name, include_self=include_self)

    def direct_declared_superclasses(self, cls_name):
        """The DIRECT declared part-hierarchy parents of `cls_name` (frozen hierarchy
        edges) with the four level roots filtered out (those are AxisPlugin's). Used by
        TaxonomyPlugin to re-emit the intermediate part->parent subClassOf edges."""
        return {p for p in self.superclasses.get(cls_name, ())
                if p not in self._LEVEL_ROOTS}

    # ---- aggregates, derived from the graph once -----------------------------
    @functools.cached_property
    def agg(self):
        return A.aggregate(self.index)

    @functools.cached_property
    def agg_uncertainty(self):
        """{ class -> { element_class -> relative_uncertainty } }: the Eq.3 aggregate
        over the reach tree, the uncertainty twin of `agg`. The propagation discipline is
        read from the ruleset's uncertaintyPropagation facet; empty when no ruleset."""
        compound = _propagation_compounds(self.tbox)
        if compound is None:                       # no ruleset / no propagation facet
            return {}
        return A.aggregate_uncertainty(self.index, self.agg,
                                       compound_products=compound)

    @functools.cached_property
    def agg_dq(self):
        """{ class -> { element_class -> (mean_dq, dqs) } }: the class-level DQ
        aggregate over the composition reach tree, the DQ twin of `agg_uncertainty`
        (value-weighted mean DQ, worst DQS). Empty when the TBox carries no ruleset."""
        if _propagation_compounds(self.tbox) is None:
            return {}
        return A.aggregate_dq(self.index, self.agg)

    @functools.cached_property
    def item_mass(self):
        return A.aggregate_item_mass(self.index)

    @functools.cached_property
    def element_classes(self):
        return sorted({cls for name, cls in self.index.classes.items()
                       if self.index.levels[name] == "Element"})

    # ---- scope ---------------------------------------------------------------
    def wants(self, class_name):
        return self.want is None or class_name in self.want

    def projected_classes(self):
        """Classes a per-class plugin iterates: aggregate keys with element content,
        in scope."""
        return [c for c, per in self.agg.items() if per and self.wants(c)]

    def want_touches_structural(self):
        if self.want is None:
            return True
        return any(self.class_level(cc) in (COMPONENT, MATERIAL, PRODUCT)
                   for cc in self.want)

    # ---- TBox / index queries ------------------------------------------------
    def class_level(self, cls_name):
        """LEVEL string of a CONSTITUENT CLASS, from the TBox via rdfs:subClassOf*;
        falling back to the index's superclass edges for dynamic classes (time
        slices, data-minted) the static TBox does not contain."""
        g = self.tbox
        subj = FUT[cls_name]
        for level in LEVELS:
            if (subj, RDFS.subClassOf, FUT[level]) in g or V.subclass_of(g, subj, FUT[level]):
                return level
        sup = self.superclasses
        seen, stack = {cls_name}, list(sup.get(cls_name, ()))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for level in LEVELS:
                if cur == level or V.subclass_of(g, FUT[cur], FUT[level]):
                    return level
            stack.extend(sup.get(cur, ()))
        return None

    def slices_of_base(self, cls_name):
        """class_time entries that are a slice OF `cls_name` along ANY axis."""
        return [e for e in self.index.class_time.values()
                if any(parent == cls_name for parent, _axis in e.get("slices", ()))]

    def direct_subclasses(self, cls_name):
        sup = self.superclasses
        return sorted({c for c, sups in sup.items() if cls_name in sups})

    def strategy_token_of(self, cls_name):
        """The class's declared aggregation-strategy TOKEN, or None for a directly-
        composed (non-aggregate) class. Reads the same class_time / hier_strategies the
        aggregator uses, no axis identity."""
        return A._strategy_of(self.index, cls_name)

    def strategy_label_of(self, cls_name):
        """The rdfs:label of the class's AggregationStrategy INDIVIDUAL, read from the
        TBox (the strategy's OWN self-description — no token munging). None when the
        class has no strategy or the individual carries no label."""
        from common.vocab import strategy_individual_iri
        from rdflib import URIRef, RDFS as _RDFS
        token = A._strategy_of(self.index, cls_name)
        iri = strategy_individual_iri(token)
        if iri is None:
            return None
        lbl = self.tbox.value(URIRef(iri), _RDFS.label)
        return str(lbl) if lbl is not None else None

    def descendant_leaf_classes(self, cls_name):
        sup = self.superclasses
        seen, stack, leaves = set(), [cls_name], set()
        while stack:
            cur = stack.pop()
            children = [c for c, sups in sup.items() if cur in sups]
            if not children and cur != cls_name:
                leaves.add(cur)
            for c in children:
                if c not in seen:
                    seen.add(c)
                    stack.append(c)
        return leaves

    def statement_iris_by_whole_class(self):
        """{ whole_class_localname: [PartRelation IRI, …] } off the input graph: the
        per-edge content-addressed measurement nodes, keyed by the futuram class of each
        relation's WHOLE (reached via its CompositionStatement). Pure function of graph."""
        if "stmt_by_wcls" not in self._cache:
            from rdflib import RDF
            g = self.graph_in
            out = defaultdict(list)
            for rel in g.subjects(RDF.type, FUT.PartRelation):
                cs = next(iter(g.subjects(FUT.hasPartRelation, rel)), None)
                w = g.value(predicate=FUT.hasCompositionStatement, object=cs) if cs else None
                if w is None:
                    continue
                for typ in g.objects(w, RDF.type):
                    if str(typ).startswith(str(FUT)):
                        out[V.local(typ)].append(rel)
            self._cache["stmt_by_wcls"] = out
        return self._cache["stmt_by_wcls"]

    # ---- structural traversals (memoised; the hot path) ----------------------
    def structural_adj(self):
        """whole_node -> [(part_node, best_fraction)] over step-wise statements."""
        if "adj" not in self._cache:
            adj = defaultdict(list)
            for whole, edges in self.index.adj.items():
                for e in edges:
                    adj[whole].append((e[0], e[1]))
            self._cache["adj"] = adj
        return self._cache["adj"]

    def top_and_fraction(self, node):
        """The top instance `node` rolls up to + the per-kg-of-top path fraction
        (product of step-wise LO fractions). Local walk — handles multi-instance."""
        parent, frac_from_parent = {}, {}
        for whole, edges in self.index.adj.items():
            for e in edges:
                parent[e[0]] = whole
                frac_from_parent[e[0]] = e[2]      # lo_kgkg
        cur, frac, seen = node, 1.0, set()
        while cur in parent and cur not in seen:
            seen.add(cur)
            frac *= frac_from_parent[cur]
            cur = parent[cur]
        return cur, frac

    def component_nodes(self):
        """Every component-level node as (name, cls, top_root, top_product_class)."""
        if "comp_nodes" not in self._cache:
            parent = {}
            for whole, edges in self.index.adj.items():
                for e in edges:
                    parent[e[0]] = whole

            def top_root(node):
                cur, seen = node, set()
                while cur in parent and cur not in seen:
                    seen.add(cur); cur = parent[cur]
                return cur

            out = []
            for nm, lvl in self.index.levels.items():
                if lvl == COMPONENT:
                    root = top_root(nm)
                    out.append((nm, self.index.classes[nm], root,
                                self.index.classes[root]))
            self._cache["comp_nodes"] = out
        return self._cache["comp_nodes"]

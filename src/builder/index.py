"""builder.index — build_index(graph) -> CompositionIndex.
The GRAPH-NATIVE reader: parse a composition-statement Graph into plain transient dict
INDEXES (NOT a Chain). Level inference uses subClassOf reachability over hierarchy + TBox.
"""
from __future__ import annotations

import functools
import pathlib
from collections import defaultdict
from dataclasses import dataclass, field

from rdflib import Graph, RDF, RDFS, URIRef

from common import pipeline, vocab
from common.vocab import (FUT, CEONQ, QUDT, TIME, DQV, UNIT_BY_NAME, LEVELS,
                          SCALE_TO_KGKG, STRATEGY_TOKEN)

# The OWL level classes, in level order — levels are read off the hierarchy
# (subClassOf reachability), not asserted as strings.
LEVEL_CLASSES = [FUT[level] for level in LEVELS]

# the four composition-level roots — too generic to aggregate over (mirrors
# model._LEVEL_ROOTS, used by ancestors_of).
_LEVEL_ROOTS = {"Product", "Component", "Material", "Element"}

_DIST_LOCAL = {
    "triangular": "TriangularDistribution",
    "uniform": "UniformDistribution",
    "rectangular": "RectangularDistribution",
    "normal": "NormalDistribution",
    "lognormal": "LogNormalDistribution",
    "beta": "BetaDistribution",
    "gamma": "GammaDistribution",
    "weibull": "WeibullDistribution",
}
_LOCAL_TO_DIST = {v: k for k, v in _DIST_LOCAL.items()}

_UNIT_LOCAL_TO_NAME = {str(u).split("/")[-1]: name
                       for name, u in UNIT_BY_NAME.items()}

# bounded distribution kinds carry a hard lower bound = lo; centre-based kinds
# do not, so their lightest credible mass is the best value (mirrors
# model.Stmt.has_hard_lower_bound).
_HARD_LOWER_BOUND = {"triangular", "uniform", "rectangular"}


def _local(iri):
    s = str(iri)
    return s.split("#")[-1] if "#" in s else s.split("/")[-1]


@functools.lru_cache(maxsize=1)
def _static_superclasses():
    """Direct rdfs:subClassOf edges among NAMED classes from the frozen hierarchy
    + TBox, parsed ONCE per process. The data graph's own subclass edges layer on
    top per build_index call."""
    g = Graph()
    g.parse(pipeline.HIERARCHY, format="turtle")
    g.parse(pipeline.TBOX, format="turtle")
    edges = {}
    for s, o in g.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            edges.setdefault(s, set()).add(o)
    return edges


class _ClassHierarchy:
    """Transitive rdfs:subClassOf reachability over hierarchy + TBox + the data
    graph's own subclass edges, for level inference and leaf-class picking."""

    def __init__(self, graph):
        self._sup = defaultdict(set)
        for s, sups in _static_superclasses().items():
            self._sup[s] |= sups
        for s, o in graph.subject_objects(RDFS.subClassOf):
            if isinstance(s, URIRef) and isinstance(o, URIRef):
                self._sup[s].add(o)
        self._anc = {}

    def ancestors(self, cls):
        got = self._anc.get(cls)
        if got is None:
            out, stack, seen = set(), [cls], {cls}
            while stack:
                for sup in self._sup.get(stack.pop(), ()):
                    if sup not in seen:
                        seen.add(sup)
                        out.add(sup)
                        stack.append(sup)
            got = self._anc[cls] = frozenset(out)
        return got

    def is_subclass(self, sub, sup):
        return sup in self.ancestors(sub)


def _level_of(hier, types):
    for typ in types:
        for lvl_cls, level in zip(LEVEL_CLASSES, LEVELS):
            if typ == lvl_cls or hier.is_subclass(typ, lvl_cls):
                return level
    return None


def _leaf_class(hier, types):
    types = [t for t in types
             if isinstance(t, URIRef) and str(t).startswith(str(FUT))
             and t not in LEVEL_CLASSES]
    if not types:
        return None
    for t in types:
        if not any(o != t and hier.is_subclass(o, t) for o in types):
            return _local(t)
    return _local(types[0])


def _quantity(data, q):
    """(best, lo, hi, unit_name) from a QuantityInterval node."""
    nonlocal_unit = ["kgkg"]

    def val(prop):
        for qv in data.objects(q, prop):
            nv = data.value(qv, QUDT.numericValue)
            un = data.value(qv, QUDT.unit)
            if un is not None:
                nonlocal_unit[0] = _UNIT_LOCAL_TO_NAME.get(_local(un), "kgkg")
            return float(nv) if nv is not None else None
        return None

    best = val(FUT.hasBestValue)
    lo = val(CEONQ.hasMinimalValueIncludedOfInterval)
    hi = val(CEONQ.hasMaximalValueIncludedOfInterval)
    return best, lo, hi, nonlocal_unit[0]


def _distribution(data, q):
    """(dist_short_name, params) from the interval's hasDistribution node."""
    for dist in data.objects(q, FUT.hasDistribution):
        for typ in data.objects(dist, RDF.type):
            name = _LOCAL_TO_DIST.get(_local(typ))
            if name:
                params = {}
                for p, o in data.predicate_objects(dist):
                    if p == RDF.type:
                        continue
                    # numeric distribution parameters only; object-valued facets
                    # (e.g. futuram:uncertaintyLimitStrategy, an IRI) are not params.
                    try:
                        params[_local(p)] = float(o)
                    except (TypeError, ValueError):
                        continue
                return name, params
    return "triangular", {}


@dataclass
class CompositionIndex:
    """Plain transient indexes the aggregation reads — NO methods, NO Chain. Every
    field is a dict/list of primitives; builder.aggregate's free functions read them."""
    id: str = ""
    label: str = ""

    # STEP-WISE (non-levels_skipped) adjacency: whole_name -> list of edge tuples
    #   (part, best_kgkg, lo_kgkg, hi_kgkg, floor_kgkg, dist, dist_params)
    # everything the reach walk + MC need per edge.
    adj: dict = field(default_factory=lambda: defaultdict(list))

    # COARSE (levels_skipped) statements: list of dicts {whole, part, lo_kgkg}
    # — what top_instances/unknowns plan over.
    coarse: list = field(default_factory=list)

    # node_name -> level ("Product"/"Component"/"Material"/"Element")
    levels: dict = field(default_factory=dict)
    # node_name -> futuram class localname
    classes: dict = field(default_factory=dict)
    # node_name -> float (Product/Component measured itemMass); absent otherwise
    item_mass: dict = field(default_factory=dict)

    # class -> {year | start/end, slices:[(parent,axis)], strategy?}
    class_time: dict = field(default_factory=dict)
    # class -> set(direct futuram superclass localnames): graph's own subClassOf
    # edges + the frozen futuram-hierarchy.ttl.
    superclasses: dict = field(default_factory=lambda: defaultdict(set))
    # class -> aggregation-strategy token (from the frozen hierarchy ABox).
    hier_strategies: dict = field(default_factory=dict)

    # element class -> sorted list of its Element node names (MC reads this).
    enodes_of: dict = field(default_factory=dict)

    # SIDE MAP for data-quality aggregation: (whole_name, part_name) -> (mean_dq, dqs)
    # for each NON-coarse edge, derived from the relation's DQ scores by the SAME
    # ruleset the rectangular limit uses. Absent for edges with no scores / no ruleset.
    edge_dq: dict = field(default_factory=dict)


def _floor_kgkg(best_kgkg, lo_kgkg, dist):
    """The lightest credible fraction (kg/kg): interval minimum for bounded
    kinds, else the best value (mirrors model.Stmt.floor_kgkg)."""
    return lo_kgkg if dist in _HARD_LOWER_BOUND else best_kgkg


_LIMIT_READER_CACHE = {}


def _rectangular_limit(graph, q, rel):
    """The rectangular uncertaintyLimit DERIVED from a relation's DQ scores by the
    distribution's futuram:uncertaintyLimitStrategy (weighted-sum + bands). None when
    no strategy / no scores. RulesetReader imported lazily (avoids cycle), memoised."""
    dist = graph.value(q, FUT.hasDistribution)
    if dist is None:
        return None
    strategy = graph.value(dist, FUT.uncertaintyLimitStrategy)
    if strategy is None:
        return None
    # per-dimension scores on the relation: dqv:hasQualityMeasurement -> (dim, value)
    from rdflib import Namespace
    DQV = Namespace("http://www.w3.org/ns/dqv#")
    scores = []
    for qm in graph.objects(rel, DQV.hasQualityMeasurement):
        metric = graph.value(qm, DQV.isMeasurementOf)
        val = graph.value(qm, DQV.value)
        # metric is futuram:<Dim>Score; map to its dqv:Dimension via the TBox below.
        scores.append((metric, None if val is None else float(val)))
    if not scores:
        return None
    reader = _limit_reader(strategy)
    # the reader keys weights by dqv:Dimension; translate <Dim>Score metric -> dim.
    dim_scores = [(reader.dimension_of_metric(m), v) for m, v in scores]
    return reader.limit_from_scores(dim_scores)


_DQ_READER_CACHE = []


def _dq_reader():
    """A memoised default RulesetReader over the default TBox, used to derive each
    edge's (mean_dq, dqs) from its DQ scores — the SAME rule the per-statement layer
    uses. Lazy import breaks the resolver<->index cycle. None if no ruleset present."""
    if _DQ_READER_CACHE:
        return _DQ_READER_CACHE[0]
    from rdflib import Graph
    from common import pipeline
    from builder.resolver.uncertainty import RulesetReader
    g = Graph()
    g.parse(pipeline.UNCERTAINTY_TBOX, format="turtle")
    g.parse(pipeline.TBOX, format="turtle")
    try:
        reader = RulesetReader(g)
    except ValueError:
        reader = None
    _DQ_READER_CACHE.append(reader)
    return reader


def _relation_dq(graph, rel):
    """(mean_dq, dqs) for a relation, from its per-dimension DQ scores by the default
    ruleset (mean_data_quality -> band_for_mean), mirroring the per-statement layer.
    None when there are no scores, no ruleset, or the rule rejects the vector."""
    reader = _dq_reader()
    if reader is None:
        return None
    from builder.resolver.uncertainty import _statement_scores
    scores = _statement_scores(graph, reader.g, rel)   # (dimension_iri, value) pairs
    if not scores:
        return None
    mean = reader.mean_data_quality(scores)
    if mean is None:
        return None
    band = reader.band_for_mean(mean)
    if band is None:
        return None
    dqs, _unc = band
    return mean, int(dqs)


def _limit_reader(strategy):
    """A memoised RulesetReader for `strategy`, over the default TBox (which carries
    the ruleset + dqv:inDimension links). Lazy import breaks the resolver<->index
    cycle."""
    key = str(strategy)
    reader = _LIMIT_READER_CACHE.get(key)
    if reader is None:
        from rdflib import Graph
        from common import pipeline
        from builder.resolver.uncertainty import RulesetReader
        g = Graph()
        g.parse(pipeline.UNCERTAINTY_TBOX, format="turtle")
        g.parse(pipeline.TBOX, format="turtle")
        reader = RulesetReader(g, ruleset=strategy)
        _LIMIT_READER_CACHE[key] = reader
    return reader


def _load_global_hierarchy(idx):
    """Fill idx.superclasses + idx.hier_strategies from the frozen
    futuram-hierarchy.ttl (rdfs:subClassOf among futuram classes +
    futuram:hasAggregationStrategy)."""
    hier_path = _hierarchy_path()
    if not hier_path.exists():
        return
    g = Graph().parse(str(hier_path))
    fut = str(FUT)
    for s, _, o in g.triples((None, RDFS.subClassOf, None)):
        if str(s).startswith(fut) and str(o).startswith(fut):
            idx.superclasses[str(s).split("#")[-1]].add(str(o).split("#")[-1])
    for s, o in g.subject_objects(URIRef(fut + "hasAggregationStrategy")):
        token = STRATEGY_TOKEN.get(str(o).split("#")[-1])
        if token and str(s).startswith(fut):
            idx.hier_strategies[str(s).split("#")[-1]] = token


def _hierarchy_path():
    """ontology/tbox/futuram-hierarchy.ttl under the repo root (pyproject.toml
    anchored, move-proof). Mirrors model._hierarchy_path."""
    p = pathlib.Path(__file__).resolve()
    for cand in (p, *p.parents):
        if (cand / "pyproject.toml").exists():
            return cand / "ontology" / "tbox" / "futuram-hierarchy.ttl"
    return p.parent.parent.parent.parent / "ontology" / "tbox" / "futuram-hierarchy.ttl"


def build_index(graph, sid=None) -> CompositionIndex:
    """Reconstruct the aggregation indexes from a composition-statement RDF graph: load
    the global hierarchy from futuram-hierarchy.ttl, accumulate the graph's own subclass
    edges on top, and return a CompositionIndex of plain dicts (no Chain)."""
    hier = _ClassHierarchy(graph)
    idx = CompositionIndex(id=sid or "from_graph", label=sid or "from_graph")
    _load_global_hierarchy(idx)

    # subclass_of edges between futuram classes the graph carries (instance
    # product classes under their segment, time slices under base + ancestor
    # slice). ACCUMULATE onto the global superclass map.
    for sub, sup in graph.subject_objects(RDFS.subClassOf):
        if str(sub).startswith(str(FUT)) and str(sup).startswith(str(FUT)):
            idx.superclasses[_local(sub)].add(_local(sup))

    # the time registry, read back from class annotations to_graph emits.
    def _year_of_date(lit):
        return int(str(lit)[:4])

    for cls_iri, y in graph.subject_objects(FUT.referenceYear):
        if not str(cls_iri).startswith(str(FUT)):
            continue
        cls_l = _local(cls_iri)
        entry = idx.class_time.get(cls_l)
        if entry is not None and entry.get("year") != int(y):
            raise ValueError(
                f"build_index: conflicting time scopes for class "
                f"{cls_l}: {entry} vs year {int(y)}")
        idx.class_time[cls_l] = {"year": int(y)}
    for cls_iri, per in graph.subject_objects(FUT.hasReferencePeriod):
        if not str(cls_iri).startswith(str(FUT)):
            continue
        cls_l = _local(cls_iri)
        start = end = None
        for b in graph.objects(per, TIME.hasBeginning):
            d = graph.value(b, TIME.inXSDDate)
            start = _year_of_date(d) if d is not None else None
        for e in graph.objects(per, TIME.hasEnd):
            d = graph.value(e, TIME.inXSDDate)
            end = _year_of_date(d) if d is not None else None
        if start is None or end is None:
            raise ValueError(
                f"build_index: class {cls_l} has a "
                f"futuram:hasReferencePeriod without both beginning and end")
        entry = idx.class_time.get(cls_l)
        if entry is not None and (entry.get("start"), entry.get("end")) != (start, end):
            raise ValueError(
                f"build_index: conflicting time scopes for class "
                f"{cls_l}: {entry} vs period {start}-{end}")
        idx.class_time[cls_l] = {"start": start, "end": end}

    # the GENERIC slice edges (futuram:sliceOf parent ; futuram:sliceAxis). Axis pairs
    # by the parent's nature: a TIMELESS parent -> year-slice-mean; a TIMED parent ->
    # the slice's declared non-year sliceAxis.
    timed = {_local(s) for s in graph.subjects(FUT.referenceYear, None)}
    timed |= {_local(s) for s in graph.subjects(FUT.hasReferencePeriod, None)}
    nonyear_axis = {}
    for s_iri, axis in graph.subject_objects(FUT.sliceAxis):
        tok = STRATEGY_TOKEN.get(_local(axis))
        if tok and tok != "year-slice-mean":
            nonyear_axis[_local(s_iri)] = tok
    for cls_iri, parent in graph.subject_objects(FUT.sliceOf):
        cls_l = _local(cls_iri)
        if cls_l not in idx.class_time:
            continue
        if _local(parent) in timed:
            axis = nonyear_axis.get(cls_l, "year-slice-mean")
        else:
            axis = "year-slice-mean"
        sl = idx.class_time[cls_l].setdefault("slices", [])
        edge = (_local(parent), axis)
        if edge not in sl:
            sl.append(edge)
    for cls_iri, strat in graph.subject_objects(FUT.hasAggregationStrategy):
        cls_l = _local(cls_iri)
        token = STRATEGY_TOKEN.get(_local(strat))
        if cls_l in idx.class_time and token:
            idx.class_time[cls_l]["strategy"] = token

    # nodes: every typed individual carrying an rdfs:label (the node NAME). NODE ORDER
    # matters for the MC walk (one shared rng per leaf-class/element/instance), so the
    # SAME set(graph.subjects(...)) the oracle iterates gives the SAME draw permutation.
    label_of = {}
    for node_iri in set(graph.subjects(RDF.type, None)):
        lbl = graph.value(node_iri, RDFS.label)
        if lbl is None:
            continue
        types = list(graph.objects(node_iri, RDF.type))
        level = _level_of(hier, types)
        cls = _leaf_class(hier, types)
        if level is None or cls is None:
            continue
        name = str(lbl)
        label_of[node_iri] = name
        if name not in idx.levels:
            idx.levels[name] = level
            idx.classes[name] = cls
            qv = graph.value(node_iri, FUT.itemMass)
            if qv is not None:
                nv = graph.value(qv, QUDT.numericValue)
                if nv is not None:
                    idx.item_mass[name] = float(nv)

    # statements: split into STEP-WISE adjacency edges and COARSE (levels_skipped)
    # statements, using THIS graph's node levels (the per-chain level binding).
    for rel in graph.subjects(RDF.type, FUT.PartRelation):
        # whole = the relation's owning CompositionStatement's hasCompositionStatement
        # subject; part = the relation's refersTo; quantity = on the relation.
        cs = next(iter(graph.subjects(FUT.hasPartRelation, rel)), None)
        w = graph.value(predicate=FUT.hasCompositionStatement, object=cs) if cs else None
        p = graph.value(rel, FUT.refersTo)
        if w not in label_of or p not in label_of:
            continue
        q = graph.value(rel, FUT.hasQuantity)
        best, lo, hi, unit_name = _quantity(graph, q)
        dist, params = _distribution(graph, q)

        wn = label_of[w]
        pn = label_of[p]
        unit = UNIT_BY_NAME.get(unit_name, list(UNIT_BY_NAME.values())[0])
        scale = SCALE_TO_KGKG[unit]
        # mirror model.Stmt.__post_init__: lo/hi default to best
        b = float(best)
        # RectangularDistribution stores no endpoints/half-width: its uncertaintyLimit
        # is DERIVED from the relation's DQ scores by its uncertaintyLimitStrategy, then
        # lo/hi = best x (1-/+limit). A directly-asserted uncertaintyLimit wins.
        if dist == "rectangular" and lo is None and hi is None:
            if "uncertaintyLimit" in params:
                limit = float(params["uncertaintyLimit"])
            else:
                limit = _rectangular_limit(graph, q, rel) or 0.0
            lo, hi = b * (1.0 - limit), b * (1.0 + limit)
        lo_v = b if lo is None else float(lo)
        hi_v = b if hi is None else float(hi)
        best_kgkg = b * scale
        lo_kgkg = lo_v * scale
        hi_kgkg = hi_v * scale
        floor_kgkg = _floor_kgkg(best_kgkg, lo_kgkg, dist)

        # level-skip classification (mirrors Stmt.levels_skipped over the
        # per-chain bound levels): > 1 level apart -> coarse.
        from common.vocab import LEVEL_RANK
        skipped = abs(LEVEL_RANK[idx.levels[pn]]
                      - LEVEL_RANK[idx.levels[wn]]) > 1

        if skipped:
            idx.coarse.append({"whole": wn, "part": pn, "lo_kgkg": lo_kgkg})
        else:
            idx.adj[wn].append(
                (pn, best_kgkg, lo_kgkg, hi_kgkg, floor_kgkg, dist, params))
            # parallel DQ side map (NOT part of the edge tuple): the relation's
            # (mean_dq, dqs) by the same ruleset the limit uses. Absent if no scores.
            dq = _relation_dq(graph, rel)
            if dq is not None:
                idx.edge_dq[(wn, pn)] = dq

    # element node membership grouped by element class (MC reads enodes_of).
    enodes = defaultdict(list)
    for name, lvl in idx.levels.items():
        if lvl == "Element":
            enodes[idx.classes[name]].append(name)
    idx.enodes_of = {ec: nm for ec, nm in enodes.items()}

    return idx


# ---- hierarchy helper (free function over the index dicts) -----------------
def ancestors_of(idx, cls_name, include_self=True):
    """All transitive futuram superclass local-names of `cls_name`, minus the
    four level-roots. Mirrors model.Chain.ancestors_of, reading idx.superclasses."""
    out = set()
    stack = [cls_name]
    seen = set()
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        if c != cls_name and c in _LEVEL_ROOTS:
            continue
        if include_self or c != cls_name:
            if c not in _LEVEL_ROOTS:
                out.add(c)
        for sup in idx.superclasses.get(c, ()):
            stack.append(sup)
    return out

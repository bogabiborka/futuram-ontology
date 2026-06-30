# (oracle's OWN RDF->fastchain reader: the inverse of SupplyChain.to_graph(),
#  used by the test-only golden reference. Independent of builder.load.)
# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "owlrl", "pyyaml"]
# ///
"""from_graph — reconstruct a SupplyChain from composition-statement RDF; the inverse
READER of the frozen SupplyChain.to_graph() EMITTER. Node LEVELS inferred by subClassOf
reachability against the OWL level classes (not owlrl, quadratic). Round-trips identically.
"""
import functools
import pathlib
import sys
from collections import defaultdict


from rdflib import Graph, Namespace, RDF, RDFS, URIRef

from common import pipeline
from oracle import supplychain
from oracle.fastchain import FastSupplyChain
from oracle.supplychain import (
    SupplyChain, FUT, CEONQ, QUDT, PROV, TIME, DQV, UNIT_BY_NAME, LEVELS,
)

EX = Namespace("http://example.org/futuram/")
QUDT_UNIT = Namespace("http://qudt.org/vocab/unit/")

# The OWL level classes, in the oracle's level order. Levels are read off the
# hierarchy (RDFS closure), not asserted as strings.
LEVEL_CLASSES = [FUT[level] for level in LEVELS]

# Inverse of the distribution-class emission in to_graph: FUT[<Kind>Distribution]
# -> the oracle's short dist name, built from the oracle's own dist registry.
_DIST_LOCAL = {  # short name -> TBox class local name (matches s.distribution())
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


def _local(iri):
    s = str(iri)
    return s.split("#")[-1] if "#" in s else s.split("/")[-1]


@functools.lru_cache(maxsize=1)
def _static_superclasses():
    """Direct rdfs:subClassOf edges among NAMED classes from the frozen
    hierarchy + TBox, parsed ONCE per process (they are static files). The data
    graph's own subclass edges are layered on top per from_graph call."""
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
    graph's own subclass edges. Answers only 'is A under B?', replacing the full
    owlrl RDFS closure (quadratic in LITERALS, unusable on real buckets)."""

    def __init__(self, graph):
        self._sup = defaultdict(set)
        for s, sups in _static_superclasses().items():
            self._sup[s] |= sups
        for s, o in graph.subject_objects(RDFS.subClassOf):
            if isinstance(s, URIRef) and isinstance(o, URIRef):
                self._sup[s].add(o)
        self._anc = {}

    def ancestors(self, cls):
        """All transitive named superclasses of `cls` (memoised)."""
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
    """The level (one of LEVELS) of a node, inferred from its asserted rdf:types:
    the type that IS, or is rdfs:subClassOf (transitively), a level class."""
    for typ in types:
        for lvl_cls, level in zip(LEVEL_CLASSES, LEVELS):
            if typ == lvl_cls or hier.is_subclass(typ, lvl_cls):
                return level
    return None


def _leaf_class(hier, types):
    """The most-specific (leaf) futuram class an instance node is typed with —
    the class to_graph emitted (rdf:type FUT[n.cls]). Picks the type that is NOT
    a superclass of any other of the node's types, and is not a level class."""
    types = [t for t in types
             if isinstance(t, URIRef) and str(t).startswith(str(FUT))
             and t not in LEVEL_CLASSES]
    if not types:
        return None
    # leaf = a type with no other listed type below it
    for t in types:
        if not any(o != t and hier.is_subclass(o, t) for o in types):
            return _local(t)
    return _local(types[0])


def _quantity(data, q):
    """(best, lo, hi, unit_name) from a QuantityInterval node."""
    best = lo = hi = None
    unit_name = "kgkg"

    def val(prop):
        for qv in data.objects(q, prop):
            nv = data.value(qv, QUDT.numericValue)
            un = data.value(qv, QUDT.unit)
            if un is not None:
                nonlocal_unit[0] = _UNIT_LOCAL_TO_NAME.get(_local(un), "kgkg")
            return float(nv) if nv is not None else None
        return None

    nonlocal_unit = [unit_name]
    best = val(FUT.hasBestValue)
    lo = val(CEONQ.hasMinimalValueIncludedOfInterval)
    hi = val(CEONQ.hasMaximalValueIncludedOfInterval)
    return best, lo, hi, nonlocal_unit[0]


_LIMIT_READER = []


def _rectangular_limit_from_graph(graph, q, metric_scores):
    """The rectangular uncertaintyLimit derived from a relation's DQ scores by the
    distribution's futuram:uncertaintyLimitStrategy, via the resolver's RulesetReader.
    Mirrors builder.index._rectangular_limit so the oracle bridge stays in parity."""
    dist = graph.value(q, FUT.hasDistribution)
    strategy = graph.value(dist, FUT.uncertaintyLimitStrategy) if dist is not None else None
    if strategy is None or not metric_scores:
        return None
    if not _LIMIT_READER:
        from rdflib import Graph
        from common import pipeline
        from builder.resolver.uncertainty import RulesetReader
        g = Graph()
        g.parse(pipeline.UNCERTAINTY_TBOX, format="turtle")
        g.parse(pipeline.TBOX, format="turtle")
        _LIMIT_READER.append(RulesetReader(g, ruleset=strategy))
    reader = _LIMIT_READER[0]
    dim_scores = [(reader.dimension_of_metric(m), v) for m, v in metric_scores]
    return reader.limit_from_scores(dim_scores)


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
                    # numeric params only; skip object-valued facets (e.g.
                    # futuram:uncertaintyLimitStrategy, an IRI).
                    try:
                        params[_local(p)] = float(o)
                    except (TypeError, ValueError):
                        continue
                return name, params
    return "triangular", {}


def from_graph(graph, sid=None):
    """Reconstruct a SupplyChain from a composition-statement RDF graph (what to_graph
    emits; TBox not required, hierarchy loaded internally to infer node levels by
    subClassOf reachability). Returns a FastSupplyChain (frozen oracle + caching)."""
    hier = _ClassHierarchy(graph)

    sc = FastSupplyChain(sid or "from_graph")

    # subclass_of edges between futuram classes that to_graph emitted. Recorded
    # on sc.subclass_of ONLY — the chain's `superclasses` property derives the
    # ancestor map per-chain, so the parent rolls up with NO global mutation.
    for sub, sup in graph.subject_objects(RDFS.subClassOf):
        if str(sub).startswith(str(FUT)) and str(sup).startswith(str(FUT)):
            sub_l, sup_l = _local(sub), _local(sup)
            # ACCUMULATE multiple superclasses (a time slice is subClassOf its
            # base AND the matching ancestor slice); the old `[sub] = sup`
            # silently dropped all but the last edge.
            sups = sc.subclass_of.setdefault(sub_l, [])
            if sup_l not in sups:
                sups.append(sup_l)

    # the time registry, read back from the class annotations to_graph emits
    # (time-based-classes model). Conflicting years on one class IRI mean two
    # merged graphs disagree about the same slice — fail loud.
    def _year_of_date(lit):
        return int(str(lit)[:4])

    for cls_iri, y in graph.subject_objects(FUT.referenceYear):
        if not str(cls_iri).startswith(str(FUT)):
            continue
        cls_l = _local(cls_iri)
        entry = sc.class_time.get(cls_l)
        if entry is not None and entry.get("year") != int(y):
            raise ValueError(
                f"from_graph: conflicting time scopes for class {cls_l}: "
                f"{entry} vs year {int(y)}")
        sc.class_time[cls_l] = {"year": int(y)}
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
                f"from_graph: class {cls_l} has a futuram:hasReferencePeriod "
                f"without both time:hasBeginning and time:hasEnd dates")
        entry = sc.class_time.get(cls_l)
        if entry is not None and (entry.get("start"), entry.get("end")) != (start, end):
            raise ValueError(
                f"from_graph: conflicting time scopes for class {cls_l}: "
                f"{entry} vs period {start}-{end}")
        sc.class_time[cls_l] = {"start": start, "end": end}
    # GENERIC slice edges (futuram:sliceOf parent ; sliceAxis <strategy>), carried
    # into class_time["slices"] for re-emit. A slice may slice along >1 axis; axis
    # pairs by parent nature: TIMELESS parent → year-slice-mean, TIMED → its sliceAxis.
    from oracle.fastchain import STRATEGY_TOKEN
    timed = {_local(s) for s in graph.subjects(FUT.referenceYear, None)}
    timed |= {_local(s) for s in graph.subjects(FUT.hasReferencePeriod, None)}
    nonyear_axis = {}      # slice_local -> the non-year axis token it declares
    for s_iri, axis in graph.subject_objects(FUT.sliceAxis):
        tok = STRATEGY_TOKEN.get(_local(axis))
        if tok and tok != "year-slice-mean":
            nonyear_axis[_local(s_iri)] = tok
    for cls_iri, parent in graph.subject_objects(FUT.sliceOf):
        cls_l = _local(cls_iri)
        if cls_l not in sc.class_time:
            continue
        if _local(parent) in timed:
            axis = nonyear_axis.get(cls_l, "year-slice-mean")
        else:
            axis = "year-slice-mean"
        sl = sc.class_time[cls_l].setdefault("slices", [])
        edge = (_local(parent), axis)
        if edge not in sl:
            sl.append(edge)
    for cls_iri, strat in graph.subject_objects(FUT.hasAggregationStrategy):
        cls_l = _local(cls_iri)
        token = STRATEGY_TOKEN.get(_local(strat))
        if cls_l in sc.class_time and token:
            sc.class_time[cls_l]["strategy"] = token

    # nodes: every individual that is the subject/object of a statement. Use the
    # rdfs:label (the node NAME to_graph wrote) as the SupplyChain node key.
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
        if name not in sc.nodes:
            # itemMass (absolute kg): futuram:itemMass -> QUDT QuantityValue ->
            # numericValue. Reconstructed so the pooled chain carries the anchor
            # that resolve_all projects as fq:itemMass.
            im = None
            qv = graph.value(node_iri, FUT.itemMass)
            if qv is not None:
                nv = graph.value(qv, QUDT.numericValue)
                im = float(nv) if nv is not None else None
            sc.node(name, level, cls, item_mass=im)

    # statements — GROUPED SHAPE: iterate the PartRelations under each
    # CompositionStatement. whole = the relation's CS's hasCompositionStatement
    # subject; part = refersTo; quantity on the relation.
    for rel in graph.subjects(RDF.type, FUT.PartRelation):
        cs = next(iter(graph.subjects(FUT.hasPartRelation, rel)), None)
        w = graph.value(predicate=FUT.hasCompositionStatement, object=cs) if cs else None
        p = graph.value(rel, FUT.refersTo)
        if w not in label_of or p not in label_of:
            continue
        q = graph.value(rel, FUT.hasQuantity)
        best, lo, hi, unit_name = _quantity(graph, q)
        dist, params = _distribution(graph, q)
        quality = {}
        metric_scores = []
        for qm in graph.objects(rel, DQV.hasQualityMeasurement):
            dim = graph.value(qm, DQV.isMeasurementOf)
            val = graph.value(qm, DQV.value)
            if dim is not None and val is not None:
                quality[_local(dim).replace("Score", "")] = float(val)
                metric_scores.append((dim, float(val)))
        # RectangularDistribution stores NO half-width: derive it from the DQ
        # scores via uncertaintyLimitStrategy, then lo/hi = best x (1-/+limit).
        # A directly-asserted uncertaintyLimit wins.
        if dist == "rectangular" and lo is None and hi is None and best is not None:
            limit = params.get("uncertaintyLimit")
            if limit is None:
                limit = _rectangular_limit_from_graph(graph, q, metric_scores) or 0.0
            lo, hi = best * (1.0 - float(limit)), best * (1.0 + float(limit))
        sc.stmt(
            label_of[w], label_of[p], best, lo, hi,
            unit=UNIT_BY_NAME.get(unit_name, list(UNIT_BY_NAME.values())[0]),
            dist=dist, dist_params=params,
            quality=quality,
        )

    # provenance: pull the shared individuals to_graph wrote (best-effort; only
    # needed if the rebuilt SupplyChain is re-serialised with full_metadata).
    src = graph.value(EX["src"], RDFS.label)
    agent = graph.value(EX["agent"], RDFS.label)
    proc = graph.value(EX["proc"], RDFS.label)
    period_begin = None
    period = EX["period"]
    for b in graph.objects(period, TIME.hasBeginning):
        period_begin = graph.value(b, TIME.inXSDDate)
    sc.provenance = {
        "source": str(src) if src else "rdf",
        "agent": str(agent) if agent else "rdf",
        "production": str(proc) if proc else "rdf",
        "validFrom": str(period_begin) if period_begin else "2020-01-01",
    }
    return sc


def from_turtle(path, sid=None):
    """Parse a Turtle file and reconstruct the SupplyChain. A bucket file carries only
    its year window's c-p/m-c statements; the chemistry lives ONCE in the sibling
    year-invariant material->element file (EM_SHARED_NAME), merged in automatically."""
    path = pathlib.Path(path)
    g = Graph()
    g.parse(str(path), format="turtle")
    shared = path.parent / pipeline.EM_SHARED_NAME
    if path.name != pipeline.EM_SHARED_NAME and shared.exists():
        g.parse(str(shared), format="turtle")
    return from_graph(g, sid=sid)

"""builder.aggregate — GRAPH-NATIVE aggregation over a CompositionIndex.
Free functions over a builder.index.CompositionIndex (transient dicts), NOT a Chain.
Arithmetic MIRRORS oracle/fastchain aggregation + groundtruth EXACTLY (parity 36/36).
"""
from __future__ import annotations

from collections import defaultdict

from common.vocab import (LEVELS, LEVEL_RANK, YEAR_SLICE_MEAN_IRI,
                          strategy_individual_iri)
from .index import ancestors_of

# adjacency edge-tuple field positions:
#   (part, best_kgkg, lo_kgkg, hi_kgkg, floor_kgkg, dist, dist_params)
_PART, _BEST, _LO, _HI, _FLOOR, _DIST, _DPARAMS = range(7)

# which kg/kg field a derivation reads (the `use` parameter).
_PICK = {"best": _BEST, "lo": _LO, "hi": _HI}


# ---- structural helpers ----------------------------------------------------
def top_instances(idx):
    """The top instances: nodes that are a structural whole but never a
    structural part. sorted(wholes - parts) over the step-wise adjacency."""
    wholes = set(idx.adj)
    parts = {e[_PART] for edges in idx.adj.values() for e in edges}
    return sorted(wholes - parts)


# ---- element reach ---------------------------------------------------------
# elem_reach / _elem_cls_reach: a full bottom-up pass over EVERY node, memoised ON idx
# per `use` — without it, untreaded callers recompute O(graph) inside an O(n^2) loop.
def _idx_cache(idx, name):
    cache = getattr(idx, "_reach_cache", None)
    if cache is None:
        cache = {}
        try:
            idx._reach_cache = cache
        except (AttributeError, TypeError):
            return None                     # idx forbids attrs -> no caching, correct
    return cache.setdefault(name, {})


def elem_reach(idx, use):
    """{ node_name: { element_node_name: kg-per-kg-of-node } } — for every node,
    the total fraction of each Element node reachable below it, path-product of
    `use` fractions over step-wise adjacency. One bottom-up pass, memoised per use."""
    c = _idx_cache(idx, "elem_reach")
    if c is not None and use in c:
        return c[use]
    pos = _PICK[use]
    levels = idx.levels
    memo = {}

    def vec(node):
        got = memo.get(node)
        if got is None:
            got = {}
            for e in idx.adj.get(node, ()):
                f = e[pos]
                part = e[_PART]
                if levels[part] == "Element":
                    got[part] = got.get(part, 0.0) + f
                for en, v in vec(part).items():
                    got[en] = got.get(en, 0.0) + f * v
            memo[node] = got
        return got

    for n in levels:
        vec(n)
    if c is not None:
        c[use] = memo
    return memo


def _elem_cls_reach(idx, use):
    """{ node_name: { element_CLASS: kg-per-kg-of-node } } — element reach grouped
    by element class, preserving reach-map iteration order so the sums are the
    same floats the filtered scan produced. Memoised per (idx, use)."""
    c = _idx_cache(idx, "elem_cls_reach")
    if c is not None and use in c:
        return c[use]
    classes = idx.classes
    out = {}
    for n, reach in elem_reach(idx, use).items():
        d = {}
        for en, v in reach.items():
            c2 = classes[en]
            d[c2] = d.get(c2, 0.0) + v
        out[n] = d
    if c is not None:
        c[use] = out
    return out


def element_in_whole(idx, whole, element_cls, use="best", _cls_reach=None):
    """Per-kg-of-`whole` amount (kg/kg) of `element_cls`, summed over every
    structural path down to any Element node of that class."""
    cr = _cls_reach if _cls_reach is not None else _elem_cls_reach(idx, use)
    return cr.get(whole, {}).get(element_cls, 0.0)


# ---- element-reach UNCERTAINTY (the Eq.3 twin of elem_reach) ----------------
# Error-propagation RSS over the SAME reach tree the best value walks; per reachable
# Element node carry (value, relative_u) terms. Method is DATA (compound_products flag).
_SQRT3 = 3.0 ** 0.5


def _edge_rel_u(e):
    """The leaf relative uncertainty (sigma/value) an edge carries: from its
    rectangular half-width limit = (hi-best)/best, sigma_rel = limit / sqrt(3).
    0.0 when the edge has no usable spread (hi==best or best==0)."""
    b = e[_BEST]
    hi = e[_HI]
    if b and hi is not None and hi > b:
        return ((hi - b) / b) / _SQRT3
    return 0.0


def elem_reach_uncertainty(idx, *, compound_products=False):
    """{ node: { element_node: [(value, rel_u), ...] } } — the Eq.3 twin of elem_reach.
    value = path-product of `best`; compound_products=False keeps only the leaf edge's u
    (LeafContributionRSS), True adds variances along the path. Memoised per flag."""
    c = _idx_cache(idx, "elem_reach_unc")
    key = bool(compound_products)
    if c is not None and key in c:
        return c[key]
    levels = idx.levels
    memo = {}

    def vec(node):
        got = memo.get(node)
        if got is not None:
            return got
        got = {}
        for e in idx.adj.get(node, ()):
            f = e[_BEST]
            part = e[_PART]
            u_e = _edge_rel_u(e)
            if levels[part] == "Element":
                # a leaf element edge: one path term (its value, its own u).
                got.setdefault(part, []).append((f, u_e))
            for en, terms in vec(part).items():
                for v_sub, u_sub in terms:
                    v = f * v_sub
                    if compound_products:
                        # relative variances add along the product path.
                        u = (u_e * u_e + u_sub * u_sub) ** 0.5
                    else:
                        # only the leaf edge's uncertainty survives the multiply.
                        u = u_sub
                    got.setdefault(en, []).append((v, u))
        memo[node] = got
        return got

    for n in levels:
        vec(n)
    if c is not None:
        c[key] = memo
    return memo


def element_uncertainty_over_nodes(idx, nodes, element_cls, *,
                                   compound_products=False, _reach=None):
    """Relative uncertainty of the EQUAL-MEAN amount of `element_cls` over `nodes`: pool
    each node's path terms scaled 1/n on value, contribution-weighted-RSS-combine. None
    when no content. Stamped as the amount's fq:relativeUncertainty."""
    reach = _reach if _reach is not None else \
        elem_reach_uncertainty(idx, compound_products=compound_products)
    classes = idx.classes
    n = len(nodes)
    if not n:
        return None
    pooled = []
    for nd in nodes:
        for en, terms in reach.get(nd, {}).items():
            if classes[en] == element_cls:
                for v, u in terms:
                    pooled.append((v / n, u))
    if not pooled:
        return None
    _tot, rel = _rss_combine(pooled)
    return rel


def element_uncertainty_over_nodes_weighted(idx, nodes, node_weights, element_cls, *,
                                            compound_products=False, _reach=None):
    """Relative uncertainty of the MASS-WEIGHTED amount of `element_cls` over `nodes`:
    each node's path terms are scaled by its weight (node_weights[nd] / Σweights) rather
    than 1/n. Use this when the value was computed as a mass-weighted mean, not an equal
    mean. `node_weights` is a dict {node_name: mass}; nodes with zero or missing weight
    are skipped. None when no content."""
    reach = _reach if _reach is not None else \
        elem_reach_uncertainty(idx, compound_products=compound_products)
    classes = idx.classes
    tot_weight = sum(node_weights.get(nd, 0.0) for nd in nodes)
    if tot_weight <= 0:
        return element_uncertainty_over_nodes(idx, nodes, element_cls,
                                              compound_products=compound_products,
                                              _reach=reach)
    pooled = []
    for nd in nodes:
        w = node_weights.get(nd, 0.0)
        if w <= 0:
            continue
        scale = w / tot_weight
        for en, terms in reach.get(nd, {}).items():
            if classes[en] == element_cls:
                for v, u in terms:
                    pooled.append((v * scale, u))
    if not pooled:
        return None
    _tot, rel = _rss_combine(pooled)
    return rel


def mat_reach_uncertainty(idx):
    """{ node: { material_node: [(value, rel_u), ...] } } — the uncertainty reach to
    MATERIAL-level leaves (analogous to elem_reach_uncertainty for Element-level). Stops
    at the first Material node on each path; the leaf edge's own relativeUncertainty
    (_edge_rel_u) is the uncertainty term. Memoised per idx."""
    c = _idx_cache(idx, "mat_reach_unc")
    if c is not None and True in c:
        return c[True]
    levels = idx.levels
    memo = {}

    def vec(node):
        got = memo.get(node)
        if got is not None:
            return got
        got = {}
        for e in idx.adj.get(node, ()):
            f = e[_BEST]
            part = e[_PART]
            u_e = _edge_rel_u(e)
            if levels[part] == "Material":
                got.setdefault(part, []).append((f, u_e))
            elif levels[part] in ("Product", "Component"):
                for mn, terms in vec(part).items():
                    for v_sub, u_sub in terms:
                        got.setdefault(mn, []).append((f * v_sub, u_sub))
        memo[node] = got
        return got

    for n in levels:
        vec(n)
    if c is not None:
        c[True] = memo
    return memo


def material_uncertainty_over_nodes(idx, nodes, material_cls, *, _reach=None):
    """Relative uncertainty of the EQUAL-MEAN amount of `material_cls` over `nodes`:
    pool each node's material-path terms scaled 1/n, contribution-weighted-RSS-combine.
    Mirrors element_uncertainty_over_nodes but stops the reach at Material level."""
    reach = _reach if _reach is not None else mat_reach_uncertainty(idx)
    classes = idx.classes
    n = len(nodes)
    if not n:
        return None
    pooled = []
    for nd in nodes:
        for mn, terms in reach.get(nd, {}).items():
            if classes[mn] == material_cls:
                for v, u in terms:
                    pooled.append((v / n, u))
    if not pooled:
        return None
    _tot, rel = _rss_combine(pooled)
    return rel


def _rss_combine(terms):
    """Contribution-weighted relative RSS (futuram:RootSumOfSquares) of a list of
    (value, relative_u) path terms: sqrt(sum (v*u)^2) / sum v. Returns (total_value,
    relative_uncertainty). The relative uncertainty is None when the total is 0."""
    total = 0.0
    var = 0.0
    for v, u in terms:
        total += v
        var += (v * u) ** 2
    if total <= 0:
        return total, None
    return total, (var ** 0.5) / total


def aggregate_uncertainty(idx, agg_best, *, compound_products=False):
    """Class-level RELATIVE uncertainty per (class, element_class), the Eq.3 twin of
    aggregate(). Mirrors its leaf-mean + ancestor-rollup but combines path terms by
    contribution-weighted RSS, weighting subclass sigmas by agg_best's element mass."""
    tops = top_instances(idx)
    if not tops:
        return {}
    elem_classes = sorted({cls for nm, cls in idx.classes.items()
                           if idx.levels[nm] == "Element"})
    reach = elem_reach_uncertainty(idx, compound_products=compound_products)
    classes = idx.classes

    # per node: group element-node path terms by element CLASS.
    def cls_terms(node, ec):
        out = []
        for en, terms in reach.get(node, {}).items():
            if classes[en] == ec:
                out.extend(terms)
        return out

    # 1) leaf aggregates: a class = equal mean of its instances; the mean's relative u
    #    = RSS(instance abs sigmas)/n over the mean value. Pool every instance's path
    #    terms scaled 1/n on value (contribution weights match the equal-mean value).
    leaf_roots = defaultdict(list)
    for root in tops:
        leaf_roots[idx.classes[root]].append(root)
    leaf = {}
    for cls_name, roots in leaf_roots.items():
        n = len(roots)
        per_elem = {}
        for ec in elem_classes:
            pooled = []
            for r in roots:
                for v, u in cls_terms(r, ec):
                    pooled.append((v / n, u))      # equal-mean: each instance /n
            if pooled:
                _tot, rel = _rss_combine(pooled)
                if rel is not None:
                    per_elem[ec] = rel
        leaf[cls_name] = per_elem

    out = {c: dict(d) for c, d in leaf.items()}

    # 2) ancestor aggregates: equal mean of the direct subclasses with an aggregate,
    #    gated identically to aggregate(). Parent rel u = RSS(subclass abs sigmas
    #    best_sub*rel_sub) / subclass count, made relative against agg_best[parent].
    ancestors = set()
    for cls_name in leaf_roots:
        ancestors |= (ancestors_of(idx, cls_name) - {cls_name})
    anc_family = defaultdict(set)
    for cls_name, roots in leaf_roots.items():
        fam = {idx.levels[r] for r in roots}
        for a in ancestors_of(idx, cls_name) - {cls_name}:
            anc_family[a] |= fam

    def subclasses_with_agg(parent, computed):
        return sorted({c for c in (set(leaf) | set(computed))
                       if parent in idx.superclasses.get(c, ())})

    computed = {}
    remaining = set(ancestors)
    for _ in range(len(remaining) + 1):
        progressed = False
        for p in sorted(remaining):
            subs = subclasses_with_agg(p, computed)
            if not subs:
                continue
            fam_pc = bool(anc_family.get(p, set()) & {"Product", "Component"})
            subs = parent_gate(idx, p, fam_pc, subs)
            if subs is None:
                remaining.discard(p); progressed = True; continue
            if not subs:
                continue
            per_elem = {}
            for ec in elem_classes:
                # absolute sigma of each subclass = best_sub * rel_sub.
                abs_sigmas = []
                for s in subs:
                    rel_s = (leaf.get(s) or computed.get(s) or {}).get(ec)
                    best_s = (agg_best.get(s) or {}).get(ec)
                    if rel_s is not None and best_s is not None:
                        abs_sigmas.append(best_s * rel_s)
                if not abs_sigmas:
                    continue
                parent_best = (agg_best.get(p) or {}).get(ec)
                if not parent_best:
                    continue
                k = len(subs)
                # equal mean -> sigma = RSS(abs_sigmas)/k ; relative to parent best.
                sig = (sum(s * s for s in abs_sigmas) ** 0.5) / k
                per_elem[ec] = sig / parent_best
            computed[p] = per_elem
            out[p] = dict(per_elem)
            remaining.discard(p); progressed = True
        if not progressed:
            break
    return out


# ---- element-reach DATA QUALITY (the DQ twin of the uncertainty reach) ------
# Two facets up the SAME reach tree: fq:dqs = WORST (max) leaf DQS; fq:meanDataQuality
# = value-weighted mean of mean DQ. Per node carry (value, mean_dq, dqs) leaf terms.
def elem_reach_dq(idx):
    """{ node_name: { element_node_name: [(path_value, mean_dq, dqs), ...] } } — the
    DQ twin of elem_reach_uncertainty. value = path-product of `best`; mean_dq/dqs are
    the leaf element edge's own DQ from idx.edge_dq. One bottom-up memoised pass."""
    c = _idx_cache(idx, "elem_reach_dq")
    if c is not None and True in c:
        return c[True]
    levels = idx.levels
    edge_dq = getattr(idx, "edge_dq", {}) or {}
    memo = {}

    def vec(node):
        got = memo.get(node)
        if got is not None:
            return got
        got = {}
        for e in idx.adj.get(node, ()):
            f = e[_BEST]
            part = e[_PART]
            if levels[part] == "Element":
                dq = edge_dq.get((node, part))
                if dq is not None:
                    mean_dq, dqs = dq
                    got.setdefault(part, []).append((f, mean_dq, dqs))
            for en, terms in vec(part).items():
                for v_sub, mean_sub, dqs_sub in terms:
                    got.setdefault(en, []).append((f * v_sub, mean_sub, dqs_sub))
        memo[node] = got
        return got

    for n in levels:
        vec(n)
    if c is not None:
        c[True] = memo
    return memo


def _dq_combine(terms):
    """Value-weighted mean of mean_dq + max of dqs over (value, mean_dq, dqs) path
    terms. Returns (total_value, (mean_dq, dqs)) or (total, None) when empty/zero."""
    total = 0.0
    wsum = 0.0
    worst = None
    for v, mean_dq, dqs in terms:
        total += v
        wsum += v * mean_dq
        worst = dqs if worst is None else max(worst, dqs)
    if total <= 0 or worst is None:
        return total, None
    return total, (wsum / total, worst)


def element_dq_over_nodes(idx, nodes, element_cls, *, _reach=None):
    """(mean_dq, dqs) of the EQUAL-MEAN amount of `element_cls` over `nodes` — the DQ
    twin of element_uncertainty_over_nodes, over the SAME node set. mean_dq = value-
    weighted mean of the leaves' means; dqs = worst (max). None when no DQ present."""
    reach = _reach if _reach is not None else elem_reach_dq(idx)
    classes = idx.classes
    n = len(nodes)
    if not n:
        return None
    pooled = []
    for nd in nodes:
        for en, terms in reach.get(nd, {}).items():
            if classes[en] == element_cls:
                for v, mean_dq, dqs in terms:
                    pooled.append((v / n, mean_dq, dqs))
    if not pooled:
        return None
    _tot, dq = _dq_combine(pooled)
    return dq


def aggregate_dq(idx, agg_best):
    """Class-level (mean_dq, dqs) per (class, element_class), the DQ twin of
    aggregate_uncertainty(). Leaves pool instance terms (mean_dq = value-weighted mean,
    dqs = max); ancestors roll up the same, weighting mean_dq by agg_best, dqs = max."""
    tops = top_instances(idx)
    if not tops:
        return {}
    elem_classes = sorted({cls for nm, cls in idx.classes.items()
                           if idx.levels[nm] == "Element"})
    reach = elem_reach_dq(idx)
    classes = idx.classes

    def cls_terms(node, ec):
        out = []
        for en, terms in reach.get(node, {}).items():
            if classes[en] == ec:
                out.extend(terms)
        return out

    # 1) leaf aggregates: a class = equal mean of its instances. Pool every instance's
    #    path terms scaled by 1/n on value (the contribution weights match the equal-
    #    mean value), then value-weighted-mean the mean_dq and max the dqs.
    leaf_roots = defaultdict(list)
    for root in tops:
        leaf_roots[idx.classes[root]].append(root)
    leaf = {}
    for cls_name, roots in leaf_roots.items():
        n = len(roots)
        per_elem = {}
        for ec in elem_classes:
            pooled = []
            for r in roots:
                for v, mean_dq, dqs in cls_terms(r, ec):
                    pooled.append((v / n, mean_dq, dqs))
            if pooled:
                _tot, dq = _dq_combine(pooled)
                if dq is not None:
                    per_elem[ec] = dq
        leaf[cls_name] = per_elem

    out = {c: dict(d) for c, d in leaf.items()}

    # 2) ancestor aggregates: equal mean of the direct subclasses with a DQ, gated
    #    identically to aggregate(). The parent's mean_dq is the subclass mean_dqs
    #    weighted by the absolute element mass each contributes (agg_best); dqs is max.
    ancestors = set()
    for cls_name in leaf_roots:
        ancestors |= (ancestors_of(idx, cls_name) - {cls_name})
    anc_family = defaultdict(set)
    for cls_name, roots in leaf_roots.items():
        fam = {idx.levels[r] for r in roots}
        for a in ancestors_of(idx, cls_name) - {cls_name}:
            anc_family[a] |= fam

    def subclasses_with_agg(parent, computed):
        return sorted({c for c in (set(leaf) | set(computed))
                       if parent in idx.superclasses.get(c, ())})

    computed = {}
    remaining = set(ancestors)
    for _ in range(len(remaining) + 1):
        progressed = False
        for p in sorted(remaining):
            subs = subclasses_with_agg(p, computed)
            if not subs:
                continue
            fam_pc = bool(anc_family.get(p, set()) & {"Product", "Component"})
            subs = parent_gate(idx, p, fam_pc, subs)
            if subs is None:
                remaining.discard(p); progressed = True; continue
            if not subs:
                continue
            per_elem = {}
            for ec in elem_classes:
                wsum = 0.0
                wtot = 0.0
                worst = None
                for s in subs:
                    dq_s = (leaf.get(s) or computed.get(s) or {}).get(ec)
                    best_s = (agg_best.get(s) or {}).get(ec)
                    if dq_s is None or best_s is None:
                        continue
                    mean_s, dqs_s = dq_s
                    wsum += best_s * mean_s
                    wtot += best_s
                    worst = dqs_s if worst is None else max(worst, dqs_s)
                if worst is None or wtot <= 0:
                    continue
                per_elem[ec] = (wsum / wtot, worst)
            computed[p] = per_elem
            out[p] = dict(per_elem)
            remaining.discard(p); progressed = True
        if not progressed:
            break
    return out


# ---- time-based gating -----------------------------------------------------
def _time_scope(entry):
    if "year" in entry:
        return entry["year"], entry["year"]
    return entry["start"], entry["end"]


def _slice_parents(entry):
    return [parent for parent, _axis in entry.get("slices", ())]


def _strategy_of(idx, cls_name):
    """The class's declared aggregation-strategy token: its class_time entry
    first, else the frozen hierarchy ABox's declaration."""
    entry = idx.class_time.get(cls_name)
    if entry and "strategy" in entry:
        return entry["strategy"]
    return idx.hier_strategies.get(cls_name)


def _strategy_iri_of(idx, cls_name):
    """The class's aggregation-strategy as the IRI of its individual, or None.
    Semantic dispatch compares THIS, not the string token."""
    return strategy_individual_iri(_strategy_of(idx, cls_name))


def check_time_complete(idx, leaf_roots):
    """Every Product/Component LEAF class must be time-scoped. Loud backstop —
    SHACL S1 reports the same at graph level (aggregation.py:49-63)."""
    missing = sorted(
        c for c, roots in leaf_roots.items()
        if c not in idx.class_time
        and any(idx.levels[r] in ("Product", "Component")
                for r in roots))
    if missing:
        raise ValueError(
            f"scenario {idx.id or idx.label!r}: Product/Component class(es) "
            f"with instances but no class_time entry (time-based classes "
            f"require a reference year or period): {missing}")


def parent_gate(idx, parent, fam_pc, candidates):
    """Which of `candidates` (direct subclasses with an aggregate) the parent may
    average over. None when the parent gets NO aggregate. (aggregation.py:65-97)"""
    if not fam_pc:
        return candidates
    p_entry = idx.class_time.get(parent)
    if p_entry is not None:
        p0, p1 = _time_scope(p_entry)
        return [c for c in candidates if c in idx.class_time
                and p0 <= _time_scope(idx.class_time[c])[0]
                and _time_scope(idx.class_time[c])[1] <= p1]
    strat = _strategy_iri_of(idx, parent)
    if strat == YEAR_SLICE_MEAN_IRI or (
            strat is None
            and any(parent in _slice_parents(e)
                    for e in idx.class_time.values())):
        return [c for c in candidates if c in idx.class_time]
    return None


# ---- composition aggregate -------------------------------------------------
def aggregate(idx, use="best"):
    """Class-level composition, aggregated recursively up the class tree.
    Returns { class_name: { element_class: amount } }. (aggregation.py:300-406)"""
    tops = top_instances(idx)
    if not tops:
        return {}
    elem_classes = sorted({cls for nm, cls in idx.classes.items()
                           if idx.levels[nm] == "Element"})
    cls_reach = _elem_cls_reach(idx, use)

    # 1) leaf aggregates: equal mean of each declared class's instances
    leaf_roots = defaultdict(list)
    for root in tops:
        leaf_roots[idx.classes[root]].append(root)
    check_time_complete(idx, leaf_roots)
    anc_family = defaultdict(set)
    for cls_name, roots in leaf_roots.items():
        fam = {idx.levels[r] for r in roots}
        for a in ancestors_of(idx, cls_name) - {cls_name}:
            anc_family[a] |= fam
    leaf = {}
    for cls_name, roots in leaf_roots.items():
        n = len(roots)
        per_elem = {}
        for ec in elem_classes:
            acc = sum(element_in_whole(idx, r, ec, use, cls_reach)
                      for r in roots)
            amount = acc / n if n else 0.0
            if amount > 0:
                per_elem[ec] = amount
        leaf[cls_name] = per_elem

    # 2) recursively build ancestor aggregates as the EQUAL mean of the direct
    #    subclasses that have an aggregate.
    out = {c: {e: round(v, 9) for e, v in d.items()} for c, d in leaf.items()}
    ancestors = set()
    for cls_name in leaf_roots:
        ancestors |= (ancestors_of(idx, cls_name) - {cls_name})

    def subclasses_with_agg(parent, computed):
        return sorted({c for c in (set(leaf) | set(computed))
                       if parent in idx.superclasses.get(c, ())})

    computed = {}
    remaining = set(ancestors)
    for _ in range(len(remaining) + 1):
        progressed = False
        for p in sorted(remaining):
            subs = subclasses_with_agg(p, computed)
            if not subs:
                continue
            fam_pc = bool(anc_family.get(p, set()) & {"Product", "Component"})
            subs = parent_gate(idx, p, fam_pc, subs)
            if subs is None:
                remaining.discard(p)
                progressed = True
                continue
            if not subs:
                continue
            per_elem = {}
            for ec in elem_classes:
                vals = [(leaf.get(s) or computed.get(s))[ec]
                        for s in subs if ec in (leaf.get(s) or computed.get(s) or {})]
                if vals:
                    per_elem[ec] = sum(vals) / len(vals)
            computed[p] = per_elem
            out[p] = {e: round(v, 9) for e, v in per_elem.items()}
            remaining.discard(p)
            progressed = True
        if not progressed:
            break
    return out


# ---- item-mass aggregate ---------------------------------------------------
def aggregate_item_mass(idx):
    """Class-level item mass (absolute kg), parallel to aggregate().
    Returns { class_name: kg }. (aggregation.py:431-487)"""
    by_cls = defaultdict(list)
    for name, im in idx.item_mass.items():
        if idx.levels.get(name) in ("Product", "Component") and im is not None:
            by_cls[idx.classes[name]].append(name)
    leaf = {}
    for cls_name, names in by_cls.items():
        n = len(names)
        if not n:
            continue
        leaf[cls_name] = sum(idx.item_mass[r] for r in names) / n

    out = {c: round(v, 9) for c, v in leaf.items()}
    anc_family = defaultdict(set)
    for cls_name in by_cls:
        for a in ancestors_of(idx, cls_name) - {cls_name}:
            anc_family[a] |= {idx.levels[by_cls[cls_name][0]]}
    ancestors = set()
    for cls_name in by_cls:
        ancestors |= (ancestors_of(idx, cls_name) - {cls_name})

    def subclasses_with_mass(parent, computed):
        return sorted({c for c in (set(leaf) | set(computed))
                       if parent in idx.superclasses.get(c, ())})

    computed = {}
    remaining = set(ancestors)
    for _ in range(len(remaining) + 1):
        progressed = False
        for p in sorted(remaining):
            subs = subclasses_with_mass(p, computed)
            if not subs:
                continue
            fam_pc = bool(anc_family.get(p, set()) & {"Product", "Component"})
            subs = parent_gate(idx, p, fam_pc, subs)
            if subs is None:
                remaining.discard(p); progressed = True; continue
            if not subs:
                continue
            vals = [(leaf.get(s) if s in leaf else computed.get(s))
                    for s in subs]
            vals = [v for v in vals if v is not None]
            if vals:
                m = sum(vals) / len(vals)
                computed[p] = m
                out[p] = round(m, 9)
            remaining.discard(p); progressed = True
        if not progressed:
            break
    return out


# ---- ground-truth: unknown-chain planning ----------------------------------
def _granular_sum(idx, whole, part):
    """Sum of minimum kg/kg of `part` reaching `whole` via multi-hop step-wise
    paths (path-product of floor fractions). (groundtruth.py:89-113)"""
    total = 0.0

    def walk(cur, acc):
        nonlocal total
        for e in idx.adj[cur]:
            f = acc * e[_FLOOR]
            if e[_PART] == part:
                total += f
            else:
                walk(e[_PART], f)
    walk(whole, 1.0)
    return total


def _top_of(idx, node):
    """The root whole of the tree `node` sits in. (groundtruth.py:131-142)"""
    parent = {e[_PART]: w for w, edges in idx.adj.items() for e in edges}
    cur, seen = node, set()
    while cur in parent and cur not in seen:
        seen.add(cur)
        cur = parent[cur]
    return cur


def _path_fraction(idx, target, top=None):
    """Minimum path fraction from `top` down to `target` (product of step-wise lo
    fractions). 1.0 for the top itself, 0.0 if unreachable. (groundtruth.py:144-166)"""
    if top is None:
        top = _top_of(idx, target)
    if target == top:
        return 1.0
    best = [0.0]

    def walk(cur, acc):
        for e in idx.adj[cur]:
            if e[_PART] == target:
                best[0] = max(best[0], acc * e[_LO])
            else:
                walk(e[_PART], acc * e[_LO])
    walk(top, 1.0)
    return best[0]


def _known_path_levels(idx, whole, part):
    """The sequence of node LEVELS strictly between `whole` and `part` along a
    known structural path (deepest path); else canonical rank-based levels.
    (groundtruth.py:168-196)"""
    found = []

    def walk(cur, trail):
        for e in idx.adj[cur]:
            if e[_PART] == part:
                found.append(list(trail))
            else:
                walk(e[_PART], trail + [idx.levels[e[_PART]]])
    walk(whole, [])
    if found:
        return max(found, key=len)
    w_rank = LEVEL_RANK[idx.levels[whole]]
    p_rank = LEVEL_RANK[idx.levels[part]]
    return [LEVELS[r] for r in range(w_rank + 1, p_rank)]


def unknowns(idx):
    """PLAN the disjoint unknown chains the reconcile rule must DERIVE. Returns a
    list of dicts {whole, part, amount, fillers, chain}. (groundtruth.py:198-263,
    including the multi-instance per-tree deepest-first subtraction.)"""
    coarse = {}
    for c in idx.coarse:
        coarse[(c["whole"], c["part"])] = c["lo_kgkg"]

    plans = []
    parts = {p for (_, p) in coarse}
    for part in sorted(parts):
        by_top = defaultdict(list)
        for (w, p) in coarse:
            if p == part:
                by_top[_top_of(idx, w)].append(w)
        for top in sorted(by_top):
            bounders = by_top[top]
            bounders.sort(key=lambda w: LEVEL_RANK[idx.levels[w]],
                          reverse=True)
            known_proj = _granular_sum(idx, top, part)
            deeper_proj = known_proj
            for w in bounders:
                cproj = coarse[(w, part)] * _path_fraction(idx, w, top)
                amount = cproj - deeper_proj
                deeper_proj = cproj
                if amount <= 1e-12:
                    continue
                fillers = _known_path_levels(idx, w, part)
                chn = [w] + [f"unknown{lv}" for lv in fillers] + [part]
                plans.append({
                    "whole": w,
                    "part": part,
                    "amount": round(amount, 6),
                    "fillers": fillers,
                    "chain": chn,
                })
    return plans

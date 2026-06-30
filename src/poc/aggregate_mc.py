"""poc.aggregate_mc — Monte-Carlo class-level aggregation over a CompositionIndex
(poc EXTENDS the builder; the core builder does NO MC). Reimplements the oracle's
montecarlo.py arithmetic verbatim on the kg/kg index (no unit re-scaling).
"""
from __future__ import annotations

import math
import random
from collections import defaultdict

from builder.index import ancestors_of
from builder.aggregate import (top_instances,
                               check_time_complete, parent_gate)

# adjacency edge-tuple field positions (mirror aggregate.py):
#   (part, best_kgkg, lo_kgkg, hi_kgkg, floor_kgkg, dist, dist_params)
_PART, _BEST, _LO, _HI, _FLOOR, _DIST, _DPARAMS = range(7)


def _sample_edge(e, rng):
    """One Monte-Carlo draw (kg/kg) of adjacency edge `e` from its distribution
    (centre=best, shape per kind), clamped to >= 0. Values are ALREADY kg/kg in the
    index, so no unit scaling. (montecarlo.py:14-48, verbatim arithmetic.)"""
    kind = e[_DIST]
    best, lo, hi, params = e[_BEST], e[_LO], e[_HI], e[_DPARAMS]
    if kind == "triangular":
        v = best if hi <= lo else rng.triangular(lo, hi, best)
    elif kind in ("uniform", "rectangular"):
        # rectangular == uniform; its lo/hi were derived from best x (1 -/+ limit)
        # at index time, so the draw is the same uniform on [lo, hi].
        v = best if hi <= lo else rng.uniform(lo, hi)
    elif kind == "normal":
        v = rng.gauss(best, params["stdDev"])
    elif kind == "lognormal":
        mu = math.log(best) if best > 0 else 0.0
        v = rng.lognormvariate(mu, params["logStdDev"]) if best > 0 else 0.0
    elif kind == "beta":
        b = rng.betavariate(params["alpha"], params["beta"])
        v = lo + b * (hi - lo)
    elif kind == "gamma":
        v = rng.gammavariate(params["shapeParam"], params["scaleParam"])
    elif kind == "weibull":
        v = rng.weibullvariate(params["scaleParam"], params["shapeParam"])
    else:
        v = best
    return max(0.0, v)


def _instance_element_sample(idx, root, element_name, rng):
    """One MC draw of the per-kg total of `element_name` reaching `root`, sampling
    every edge on every path. Edges walked in CANONICAL (sorted-by-part) order so the
    shared `rng` is consumed graph-deterministically (not PYTHONHASHSEED-dependent)."""
    total = [0.0]

    def walk(cur, acc):
        for e in sorted(idx.adj.get(cur, ()), key=lambda e: e[_PART]):
            f = acc * _sample_edge(e, rng)
            if e[_PART] == element_name:
                total[0] += f
            else:
                walk(e[_PART], f)
    walk(root, 1.0)
    return total[0]


def aggregate_mc(idx, samples=10000, percentiles=(5, 95), seed=42,
                 scope_class=None):
    """Monte-Carlo class-level aggregation (distributional counterpart of aggregate()):
    per draw, sample every edge, path-multiply, equal-mean across instances (and
    subclasses). Returns {class: {element: {best=median, lo, hi=percentiles}}}."""
    tops = top_instances(idx)
    if not tops:
        return {}
    leaf_roots = defaultdict(list)
    for root in tops:
        leaf_roots[idx.classes[root]].append(root)
    # On-demand SCOPING (the cache path): keep only instances that roll UP into
    # scope_class — i.e. scope_class is an ancestor of their leaf class
    # (ancestors_of includes self, so a leaf scope keeps its own instances).
    if scope_class is not None:
        leaf_roots = defaultdict(list, {
            cls: roots for cls, roots in leaf_roots.items()
            if scope_class in ancestors_of(idx, cls)
        })
        if not leaf_roots:
            return {}
    check_time_complete(idx, leaf_roots)
    anc_family = defaultdict(set)
    for cls_name, roots in leaf_roots.items():
        fam = {idx.levels[r] for r in roots}
        for a in ancestors_of(idx, cls_name) - {cls_name}:
            anc_family[a] |= fam
    if scope_class is None:
        elem_classes = sorted({cls for nm, cls in idx.classes.items()
                               if idx.levels[nm] == "Element"})
    else:
        # only element classes actually reachable from the scoped roots.
        reachable, seen_part = set(), set()
        stack = [r for roots in leaf_roots.values() for r in roots]
        while stack:
            cur = stack.pop()
            for e in idx.adj.get(cur, ()):
                part = e[_PART]
                if idx.levels.get(part) == "Element":
                    reachable.add(idx.classes[part])
                elif part not in seen_part:
                    seen_part.add(part)
                    stack.append(part)
        elem_classes = sorted(reachable)
    # sorted node lists: the sampler consumes the rng per element node, so a
    # canonical order makes the seeded draw independent of idx.levels insertion
    # order (which for a graph-built index follows rdflib/hash-seed iteration).
    enodes_of = {ec: sorted(nm for nm, lvl in idx.levels.items()
                            if lvl == "Element" and idx.classes[nm] == ec)
                 for ec in elem_classes}
    rng = random.Random(seed)

    def pct(sorted_vals, p):
        if not sorted_vals:
            return 0.0
        k = (len(sorted_vals) - 1) * (p / 100.0)
        f = int(k); c = min(f + 1, len(sorted_vals) - 1)
        return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)

    # 1) per (leaf class, element): a draw VECTOR (length=samples), each draw the
    # EQUAL (unweighted) mean of the class's own instances.
    draws = defaultdict(dict)
    for cls_name, roots in leaf_roots.items():
        n = len(roots)
        for ec in elem_classes:
            vec = []
            for _ in range(samples):
                acc = 0.0
                for r in roots:
                    acc += sum(_instance_element_sample(idx, r, en, rng)
                               for en in enodes_of[ec])
                vec.append(acc / n if n else 0.0)
            draws[cls_name][ec] = vec

    # 2) parent classes: equal-mean of direct subclasses' draw vectors, index-by-
    # index, to a fixpoint (mirrors aggregate()).
    ancestors = set()
    for cls_name in leaf_roots:
        ancestors |= (ancestors_of(idx, cls_name) - {cls_name})

    def subclasses_with_draws(parent):
        return sorted({c for c in draws
                       if parent in idx.superclasses.get(c, ())})

    remaining = set(ancestors)
    for _ in range(len(remaining) + 1):
        progressed = False
        for p in sorted(remaining):
            subs = subclasses_with_draws(p)
            if not subs:
                continue
            fam_pc = bool(anc_family.get(p, set()) & {"Product", "Component"})
            subs = parent_gate(idx, p, fam_pc, subs)
            if subs is None:
                remaining.discard(p); progressed = True; continue
            if not subs:
                continue
            for ec in elem_classes:
                have = [draws[s][ec] for s in subs if ec in draws[s]]
                if have:
                    draws[p][ec] = [sum(col) / len(col) for col in zip(*have)]
            remaining.discard(p); progressed = True
        if not progressed:
            break

    # 3) collapse each (class, element) draw vector to median + percentiles.
    out = {}
    for cls_name, per_ec in draws.items():
        per_elem = {}
        for ec, vec in per_ec.items():
            sv = sorted(vec)
            med = pct(sv, 50)
            if med > 1e-12:
                per_elem[ec] = {
                    "best": round(med, 6),
                    "lo": round(pct(sv, percentiles[0]), 6),
                    "hi": round(pct(sv, percentiles[1]), 6),
                }
        out[cls_name] = per_elem
    return out

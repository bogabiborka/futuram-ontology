# (fastchain package — split from the single-file implementation; the
#  arithmetic is verbatim, only the module layout changed.)
"""Monte-Carlo class-level aggregation.
Mixed into SupplyChain (chain.py).
"""
from collections import defaultdict

from .hierarchy import ancestors_of
from .vocab import SCALE_TO_KGKG

class MonteCarloMixin:
    # ---- Monte-Carlo aggregation ----------------------------------------
    def _sample_stmt(self, s, rng):
        """One Monte-Carlo draw (kg/kg) of statement `s`: centre = best, shape +
        params per the distribution kind (bounded kinds use lo/hi, centre-based
        use their spread param). Clamped to >= 0 (a fraction cannot be negative)."""
        import math
        scale = SCALE_TO_KGKG[s.unit]
        kind = s.dist
        if kind == "triangular":
            lo, hi, mode = s.lo, s.hi, s.best
            if hi <= lo:
                v = mode
            else:
                v = rng.triangular(lo, hi, mode)
        elif kind in ("uniform", "rectangular"):
            # rectangular == uniform; its lo/hi were derived from best x (1 -/+ limit).
            v = rng.uniform(s.lo, s.hi)
        elif kind == "normal":
            v = rng.gauss(s.best, s.dist_params["stdDev"])
        elif kind == "lognormal":
            # median = best value -> mu = ln(best); sigma = logStdDev
            mu = math.log(s.best) if s.best > 0 else 0.0
            v = rng.lognormvariate(mu, s.dist_params["logStdDev"]) if s.best > 0 else 0.0
        elif kind == "beta":
            # beta on [0,1] scaled to [lo, hi]
            b = rng.betavariate(s.dist_params["alpha"], s.dist_params["beta"])
            v = s.lo + b * (s.hi - s.lo)
        elif kind == "gamma":
            v = rng.gammavariate(s.dist_params["shapeParam"],
                                 s.dist_params["scaleParam"])
        elif kind == "weibull":
            v = rng.weibullvariate(s.dist_params["scaleParam"],
                                   s.dist_params["shapeParam"])
        else:
            v = s.best
        return max(0.0, v) * scale

    def _instance_element_sample(self, root, element_name, rng):
        """One MC draw of the per-kg total of `element_name` reaching `root`,
        sampling each statement on each path. Edges walked in CANONICAL
        (sorted-by-part) order so the seeded `rng` stays a pure function."""
        adj = self._adj()
        total = [0.0]

        def walk(cur, acc):
            for s in sorted(adj[cur], key=lambda s: s.part):
                f = acc * self._sample_stmt(s, rng)
                if s.part == element_name:
                    total[0] += f
                else:
                    walk(s.part, f)
        walk(root, 1.0)
        return total[0]

    def aggregate_mc(self, samples=10000, percentiles=(5, 95), seed=42,
                     scope_class=None):
        """Monte-Carlo counterpart of aggregate(): each draw samples every statement, path-multiplies to a per-instance total, equal-mean across instances. Returns {class:{element_class:{best,lo,hi}}} (best=median, lo/hi=percentiles; default 10000 samples, 5/95). MIRRORS aggregate()'s two levels: LEAF draw = EQUAL mean of own instances; PARENT draw = EQUAL mean of direct SUBCLASSES' draws index-by-index to a fixpoint — instances do NOT leak across the subclass boundary (each subclass once), NOT a flat descendant pool."""
        import random
        self._bind_levels()
        tops = self._top_instances()
        if not tops:
            return {}
        leaf_roots = defaultdict(list)
        for root in tops:
            leaf_roots[self.nodes[root].cls].append(root)
        # On-demand SCOPING: keep only instances rolling UP into scope_class (an
        # ancestor of their leaf class; ancestors_of includes self), restricting
        # the loop to one aggregate's subtree (~seconds vs the ~21h whole store).
        sup = self.superclasses
        if scope_class is not None:
            leaf_roots = defaultdict(list, {
                cls: roots for cls, roots in leaf_roots.items()
                if scope_class in ancestors_of(sup, cls)
            })
            if not leaf_roots:
                return {}
        self._check_time_complete(leaf_roots)
        # time gating mirrors aggregate(): Product/Component family ancestors
        # only aggregate within a time slice (or as a base's declared
        # year-slice mean); Material families are time-independent.
        anc_family = defaultdict(set)
        for cls_name, roots in leaf_roots.items():
            fam = {self.nodes[r].level for r in roots}
            for a in ancestors_of(sup, cls_name) - {cls_name}:
                anc_family[a] |= fam
        if scope_class is None:
            elem_classes = sorted({n.cls for n in self.nodes.values()
                                   if n.level == "Element"})
        else:
            # only element classes actually reachable from the scoped roots —
            # the breadth (n_element_classes) is the other multiplicative cost
            # factor besides instances*samples, so trimming it matters.
            adj = self._adj()
            reachable = set()
            seen_part = set()
            stack = [r for roots in leaf_roots.values() for r in roots]
            while stack:
                cur = stack.pop()
                for s in adj.get(cur, ()):
                    if self.nodes.get(s.part) and \
                            self.nodes[s.part].level == "Element":
                        reachable.add(self.nodes[s.part].cls)
                    elif s.part not in seen_part:
                        seen_part.add(s.part)
                        stack.append(s.part)
            elem_classes = sorted(reachable)
        enodes_of = {ec: sorted(nm for nm, n in self.nodes.items()
                                if n.level == "Element" and n.cls == ec)
                     for ec in elem_classes}
        rng = random.Random(seed)

        def pct(sorted_vals, p):
            if not sorted_vals:
                return 0.0
            k = (len(sorted_vals) - 1) * (p / 100.0)
            f = int(k); c = min(f + 1, len(sorted_vals) - 1)
            return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)

        # 1) per (leaf class, element): a draw VECTOR (length=samples), each draw
        # the equal mean of the class's own instances.
        draws = defaultdict(dict)  # draws[cls][ec] = [v0, v1, ...]
        for cls_name, roots in leaf_roots.items():
            cnt = len(roots)
            for ec in elem_classes:
                vec = []
                for _ in range(samples):
                    acc = 0.0
                    for r in roots:
                        acc += sum(self._instance_element_sample(r, en, rng)
                                   for en in enodes_of[ec])
                    vec.append(acc / cnt if cnt else 0.0)
                draws[cls_name][ec] = vec

        # 2) parent classes: equal-mean of direct subclasses' draw vectors,
        # index-by-index, to a fixpoint (mirrors aggregate()).
        ancestors = set()
        for cls_name in leaf_roots:
            ancestors |= (ancestors_of(sup, cls_name) - {cls_name})

        def subclasses_with_draws(parent):
            return sorted({c for c in draws
                           if parent in sup.get(c, ())})

        remaining = set(ancestors)
        for _ in range(len(remaining) + 1):
            progressed = False
            for p in sorted(remaining):
                subs = subclasses_with_draws(p)
                if not subs:
                    continue
                fam_pc = bool(anc_family.get(p, set())
                              & {"Product", "Component"})
                subs = self._parent_gate(p, fam_pc, subs)
                if subs is None:
                    # no time scope, no year-slice-mean default: no MC draws
                    remaining.discard(p)
                    progressed = True
                    continue
                if not subs:
                    continue        # qualifying subclasses not resolved yet
                for ec in elem_classes:
                    have = [draws[s][ec] for s in subs if ec in draws[s]]
                    if have:
                        draws[p][ec] = [sum(col) / len(col)
                                        for col in zip(*have)]
                remaining.discard(p)
                progressed = True
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

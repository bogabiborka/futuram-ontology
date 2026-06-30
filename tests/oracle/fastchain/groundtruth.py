"""Ground truth: conservation, coarse/fine reconciliation, unknown planning.
Mixed into SupplyChain (chain.py). (_bind_levels rebinds the shared
model._NODES_BY_NAME — the only line the package split changed.)
"""
from collections import defaultdict

from . import model
from .vocab import LEVELS, LEVEL_RANK

class GroundTruthMixin:
    # ---- ground truth ---------------------------------------------------
    def _bind_levels(self):
        model._NODES_BY_NAME = self.nodes
        # stamp each statement with ITS chain's whole/part levels so level-skip
        # classification never depends on the shared global (which leaks across
        # scenarios that reuse element names).
        for s in self.stmts:
            s.whole_level = self.nodes[s.whole].level
            s.part_level = self.nodes[s.part].level

    def conservation(self):
        """Per whole: min/max sum of its STRUCTURAL part fractions (kg/kg);
        overshoot iff the minimum exceeds 1. Coarse (level-skipping) statements
        are EXCLUDED (parallel view, not siblings); reconciled in coarse_fine()."""
        self._bind_levels()
        lo_sum = defaultdict(float)
        hi_sum = defaultdict(float)
        for s in self.stmts:
            if s.levels_skipped:
                continue
            # lightest credible fraction: hard lower bound for bounded kinds,
            # else best (centre) — mirrors the SPARQL COALESCE(min, bestValue).
            lo_sum[s.whole] += s.floor_kgkg
            hi_sum[s.whole] += s.hi_kgkg
        # epsilon 1e-6 MUST equal check-mass-conservation.rq: a sum of exactly
        # 1.0 is NOT overshoot. 1e-6 (not 1e-9) absorbs 9-dp ETL rounding noise
        # (~N x 5e-10 per part), still 4 orders below a real overshoot (>=0.01).
        return {
            w: {
                "min": lo_sum[w],
                "max": hi_sum[w],
                "overshoot": lo_sum[w] > 1.0 + 1e-6,
            }
            for w in lo_sum
        }

    def coarse_fine(self):
        """For each (whole, part) with a coarse (level-skipping) statement, compare
        the coarse total to the sum of granular (multi-hop) path contributions.
        Returns a per-pair dict with coarse, granular, unknown."""
        self._bind_levels()
        out = {}
        # index statements by (whole, part)
        by_pair = defaultdict(list)
        for s in self.stmts:
            by_pair[(s.whole, s.part)].append(s)
        for (w, p), ss in by_pair.items():
            coarse = [s for s in ss if s.levels_skipped]
            if not coarse:
                continue
            c = coarse[0]
            granular_min = self._granular_sum(w, p)
            out[(w, p)] = {
                "coarse_min": c.lo_kgkg,
                "coarse_max": c.hi_kgkg,
                "granular_min": granular_min,
                "unknown_min": max(0.0, c.lo_kgkg - granular_min),
                "overshoot": granular_min > c.hi_kgkg,
            }
        return out

    def _granular_sum(self, whole, part):
        """Sum of minimum kg/kg of `part` reaching `whole` via multi-hop paths
        (product->component->...->part), i.e. the refined attribution.
        Computed by depth-first multiply over direct adjacent statements."""
        self._bind_levels()
        adj = defaultdict(list)
        for s in self.stmts:
            if not s.levels_skipped:           # only step-wise edges form paths
                adj[s.whole].append(s)
        total = 0.0

        def walk(cur, acc):
            nonlocal total
            for s in adj[cur]:
                # 'lightest credible' fraction (as in conservation()): min
                # endpoint for bounded kinds, else best (centre kinds emit none).
                f = acc * s.floor_kgkg
                if s.part == part:
                    total += f
                else:
                    walk(s.part, f)
        walk(whole, 1.0)
        return total

    def _tops(self):
        """All root wholes (a whole that is never a part), DETERMINISTICALLY
        ordered. A single-instance scenario has one; a multi-instance scenario
        (carA/carB/carC) has several — each its own tree."""
        self._bind_levels()
        parts = {s.part for s in self.stmts if not s.levels_skipped}
        wholes = {s.whole for s in self.stmts if not s.levels_skipped}
        return sorted(wholes - parts)

    def _top(self):
        """The first root whole (deterministic). Prefer _tops()/_top_of() — this
        single-root view is correct only for a single-instance tree; kept for
        callers that genuinely have one top."""
        roots = self._tops()
        return roots[0] if roots else None

    def _top_of(self, node):
        """The root whole of the tree `node` sits in (walk parents up). In a
        multi-instance scenario this is the instance's OWN top, not an arbitrary
        global root — so per-bounder projections are deterministic."""
        self._bind_levels()
        parent = {s.part: s.whole for s in self.stmts if not s.levels_skipped}
        cur, seen = node, set()
        while cur in parent and cur not in seen:
            seen.add(cur)
            cur = parent[cur]
        return cur

    def _path_fraction(self, target, top=None):
        """Minimum path fraction from `top` (default: target's own tree root)
        down to `target` along structural edges (product of step-wise lo
        fractions). 1.0 for the top itself, 0.0 if unreachable."""
        self._bind_levels()
        if top is None:
            top = self._top_of(target)
        if target == top:
            return 1.0
        adj = defaultdict(list)
        for s in self.stmts:
            if not s.levels_skipped:
                adj[s.whole].append(s)
        best = [0.0]

        def walk(cur, acc):
            for s in adj[cur]:
                if s.part == target:
                    best[0] = max(best[0], acc * s.lo_kgkg)
                else:
                    walk(s.part, acc * s.lo_kgkg)
        walk(top, 1.0)
        return best[0]

    def _known_path_levels(self, whole, part):
        """The node LEVELS strictly between `whole` and `part` along the deepest
        known path, so an unknown chain MIRRORS the real sibling path's depth.
        Falls back to canonical rank-based levels when no known path exists."""
        self._bind_levels()
        adj = defaultdict(list)
        for s in self.stmts:
            if not s.levels_skipped:
                adj[s.whole].append(s)
        found = []

        def walk(cur, trail):
            for s in adj[cur]:
                if s.part == part:
                    found.append(list(trail))
                else:
                    walk(s.part, trail + [self.nodes[s.part].level])
        walk(whole, [])
        if found:
            # use the deepest (longest) known path's intermediate levels
            return max(found, key=len)
        # fallback: canonical levels strictly between whole and part by rank
        w_rank = LEVEL_RANK[self.nodes[whole].level]
        p_rank = LEVEL_RANK[self.nodes[part].level]
        return [LEVELS[r] for r in range(w_rank + 1, p_rank)]

    def unknowns(self):
        """PLAN the disjoint unknown chains the reconcile rule DERIVES (amounts per-1-kg-of-top, coarse_proj(W)=coarse*path_fraction). Bounders DEEPEST first, W's amount=coarse_proj(W)-coarse_proj(next-deeper), deepest minus known_proj — a DISJOINT PARTITION of coarse_proj(shallowest). Returns dicts { whole, part, amount, fillers:[levels], chain:[names] }."""
        self._bind_levels()

        coarse = {}
        for s in self.stmts:
            if s.levels_skipped:
                coarse[(s.whole, s.part)] = s.lo_kgkg

        plans = []
        parts = {p for (_, p) in coarse}
        for part in sorted(parts):
            # bounders may live in DIFFERENT instance trees; deepest-first
            # subtraction is valid only WITHIN one top tree, so group by each
            # bounder's own top and project against THAT top (deterministic).
            by_top = defaultdict(list)
            for (w, p) in coarse:
                if p == part:
                    by_top[self._top_of(w)].append(w)
            for top in sorted(by_top):
                bounders = by_top[top]
                # deepest (highest level rank) first; ties broken by path depth
                bounders.sort(key=lambda w: LEVEL_RANK[self.nodes[w].level],
                              reverse=True)
                known_proj = self._granular_sum(top, part)
                deeper_proj = known_proj   # what the next-deeper layer covers
                for w in bounders:
                    cproj = coarse[(w, part)] * self._path_fraction(w, top)
                    amount = cproj - deeper_proj
                    deeper_proj = cproj     # this layer now covers up to cproj
                    if amount <= 1e-12:
                        continue
                    # mirror the depth of the real known path from w down to part
                    fillers = self._known_path_levels(w, part)
                    chain = [w] + [f"unknown{lv}" for lv in fillers] + [part]
                    plans.append({
                        "whole": w,
                        "part": part,
                        "amount": round(amount, 6),
                        "fillers": fillers,
                        "chain": chain,
                    })
        return plans

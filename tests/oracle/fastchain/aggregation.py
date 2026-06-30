"""Class-level aggregation + the caching that keeps it linear. Mixed into
SupplyChain (chain.py): cache plumbing (_fast), cached adjacency/element-reach
passes, and the memoised aggregate()."""
from collections import defaultdict

from .hierarchy import _HIER_STRATEGIES, ancestors_of
from .vocab import _PICK

class AggregationMixin:
    # ---- time-based gating (shared by aggregate() and aggregate_mc) ------
    # P/C classes carry aggregates ONLY time-scoped, except a timeless BASE under
    # the year-slice-mean default (mean of its slices). Material/Element: unchanged.
    @staticmethod
    def _time_scope(entry):
        """(first_year, last_year) of a class_time entry."""
        if "year" in entry:
            return entry["year"], entry["year"]
        return entry["start"], entry["end"]

    @staticmethod
    def _slice_parents(entry):
        """The aggregation parents this entry is a slice OF, along any axis
        (generic; replaces the old single 'base'/'drivetrain_base' keys)."""
        return [parent for parent, _axis in entry.get("slices", ())]

    def _strategy_of(self, cls_name):
        """The class's declared aggregation-strategy token: its class_time
        entry first, else the enriched hierarchy ABox's declaration."""
        entry = self.class_time.get(cls_name)
        if entry and "strategy" in entry:
            return entry["strategy"]
        return _HIER_STRATEGIES.get(cls_name)

    def _strategy_iri_of(self, cls_name):
        """The class's aggregation-strategy as the IRI of its futuram:
        AggregationStrategy individual (the strategy's IDENTITY), or None.
        Semantic dispatch compares THIS, not the string token."""
        from .vocab import STRATEGY_INDIVIDUAL_IRI
        return STRATEGY_INDIVIDUAL_IRI(self._strategy_of(cls_name))

    def _check_time_complete(self, leaf_roots):
        """Every Product/Component LEAF class (declared class of instances)
        must be time-scoped. Loud backstop — SHACL S1 reports the same at
        graph level before the oracle ever runs."""
        missing = sorted(
            c for c, roots in leaf_roots.items()
            if c not in self.class_time
            and any(self.nodes[r].level in ("Product", "Component")
                    for r in roots))
        if missing:
            raise ValueError(
                f"scenario {self.id or self.label!r}: Product/Component "
                f"class(es) with instances but no class_time entry "
                f"(time-based classes require a reference year or period): "
                f"{missing}")

    def _parent_gate(self, parent, fam_pc, candidates):
        """Which of `candidates` the parent may average over; None when it gets
        NO aggregate. Material: ungated. Time-scoped parent: only subclasses
        within its slice. Year-slice-mean base: only its slices. Else P/C: None."""
        if not fam_pc:
            return candidates
        p_entry = self.class_time.get(parent)
        if p_entry is not None:
            p0, p1 = self._time_scope(p_entry)
            return [c for c in candidates if c in self.class_time
                    and p0 <= self._time_scope(self.class_time[c])[0]
                    and self._time_scope(self.class_time[c])[1] <= p1]
        from .vocab import YEAR_SLICE_MEAN_IRI
        strat = self._strategy_iri_of(parent)          # the strategy's IRI identity
        if strat == YEAR_SLICE_MEAN_IRI or (
                strat is None
                and any(parent in self._slice_parents(e)
                        for e in self.class_time.values())):
            # year-slice-mean is the model's declared default for a timeless
            # base (hierarchy ABox states it for every taxonomy P/C class; a
            # data-minted base falls under it by being some slice's "base").
            return [c for c in candidates if c in self.class_time]
        return None
    # ---- cache plumbing ---------------------------------------------------
    def _fast(self):
        """Cache dict for the chain's CURRENT state, invalidated when the chain
        grows (new stmt/node); must stay O(1) (hot path). Views that also depend
        on the global superclass map add _superclass_fingerprint() to their key."""
        key = (len(self.stmts), len(self.nodes))
        cache = self.__dict__.get("_fast_cache")
        if cache is None or cache["key"] != key:
            cache = {"key": key}
            self._fast_cache = cache
        return cache

    def _superclass_fingerprint(self):
        """Change-detector for THIS chain's class hierarchy. A CONTENT hash of
        the edge set (not a sum-of-lengths), so two hierarchies with equal edge
        counts can't collide on a stale cache."""
        return hash(tuple(sorted(
            (c, tuple(sorted(sups))) for c, sups in self.superclasses.items())))

    # ---- multi-instance class aggregation -------------------------------
    def _adj(self):
        """Structural adjacency (step-wise edges only), whole -> [Stmt].
        Cached: every element walk reads it, and rebuilding it per walk made
        big chains quadratic."""
        cache = self._fast()
        if "adj" not in cache:
            self._bind_levels()
            adj = defaultdict(list)
            for s in self.stmts:
                if not s.levels_skipped:
                    adj[s.whole].append(s)
            cache["adj"] = adj
        return cache["adj"]

    def _elem_reach(self, use):
        """{ node: { element_node: kg-per-kg } } — total fraction of each
        reachable Element node, summed over all paths (path-product of `use`).
        One memoised bottom-up pass; on acyclic data equals the per-pair walk."""
        cache = self._fast()
        key = ("reach", use)
        if key not in cache:
            adj = self._adj()
            pick = _PICK[use]
            nodes = self.nodes
            memo = {}

            def vec(node):
                got = memo.get(node)
                if got is None:
                    got = {}
                    for s in adj.get(node, ()):
                        f = pick(s)
                        part = s.part
                        if nodes[part].level == "Element":
                            got[part] = got.get(part, 0.0) + f
                        for en, v in vec(part).items():
                            got[en] = got.get(en, 0.0) + f * v
                    memo[node] = got
                return got

            for n in nodes:
                vec(n)
            cache[key] = memo
        return cache[key]

    def instance_element_total(self, root, element_name, use="best"):
        """Per-kg-of-`root` amount (kg/kg) of element node `element_name`, summed
        over EVERY path (path-product of step fractions). `use` = 'best'/'lo'/'hi'.
        Cached element-reach for Element targets; direct walk for others."""
        nd = self.nodes.get(element_name)
        if nd is not None and nd.level == "Element":
            return self._elem_reach(use).get(root, {}).get(element_name, 0.0)
        adj = self._adj()
        pick = _PICK[use]
        total = [0.0]

        def walk(cur, acc):
            for s in adj[cur]:
                f = acc * pick(s)
                if s.part == element_name:
                    total[0] += f
                else:
                    walk(s.part, f)
        walk(root, 1.0)
        return total[0]

    def _elem_cls_reach(self, use):
        """{ node: { element_CLASS: kg-per-kg-of-node } } — element reach
        grouped by class (one pass over _elem_reach), so element_in_whole is a
        dict lookup. Keeps the reach map's order, so sums are the same floats."""
        cache = self._fast()
        key = ("reach_cls", use)
        if key not in cache:
            nodes = self.nodes
            out = {}
            for n, reach in self._elem_reach(use).items():
                d = {}
                for en, v in reach.items():
                    c = nodes[en].cls
                    d[c] = d.get(c, 0.0) + v
                out[n] = d
            cache[key] = out
        return cache[key]

    def element_in_whole(self, whole, element_cls, use="best"):
        """Per-kg-of-`whole` amount (kg/kg) of `element_cls`, summed over every
        path to any Element node of that class. `whole` may be ANY node (the walk
        is level-agnostic). A lookup in the cached, class-grouped reach map."""
        return self._elem_cls_reach(use).get(whole, {}).get(element_cls, 0.0)

    def element_rollup(self, whole, element_cls, use="best"):
        """Element fraction in `whole` from its DIRECT children: sum of (child
        mass-fraction) * (element_cls in child). MUST equal element_in_whole
        (per-level aggregation conserves). Returns that fraction."""
        self._bind_levels()
        total = 0.0
        for s in self._adj().get(whole, []):
            child_frac = s.best_kgkg if use == "best" else (
                s.lo_kgkg if use == "lo" else s.hi_kgkg)
            if self.nodes[s.part].level == "Element":
                # a direct element child contributes its own fraction
                if self.nodes[s.part].cls == element_cls:
                    total += child_frac
            else:
                total += child_frac * self.element_in_whole(
                    s.part, element_cls, use)
        return total

    def check_element_rollup(self, use="best", tol=1e-9):
        """Mass conservation across LEVELS: for every non-leaf whole and element
        class, the rolled-up element equals element_in_whole. Raises listing any
        disagreeing (whole, element); returns True when all conserve."""
        self._bind_levels()
        elem_classes = sorted({nd.cls for nd in self.nodes.values()
                               if nd.level == "Element"})
        wholes = [n for n, nd in self.nodes.items()
                  if nd.level in ("Product", "Component", "Material")]
        bad = []
        for w in wholes:
            for ec in elem_classes:
                direct = self.element_in_whole(w, ec, use)
                rolled = self.element_rollup(w, ec, use)
                if abs(direct - rolled) > tol:
                    bad.append((w, ec, round(direct, 9), round(rolled, 9)))
        assert not bad, (
            f"scenario {self.id or self.label!r}: element roll-up does not "
            f"conserve across levels for: {bad[:8]}")
        return True

    def _element_class(self, name):
        return self.nodes[name].cls

    def aggregate(self, use="best"):
        """Memoised entry point for _aggregate_uncached: the class aggregate is
        a pure function of the chain and the global subclass map, so both are
        part of the cache key (serving, stats and tests all ask for it)."""
        cache = self._fast()
        key = ("aggregate", use, self._superclass_fingerprint())
        if key not in cache:
            cache[key] = self._aggregate_uncached(use)
        return cache[key]

    def _aggregate_uncached(self, use="best"):
        """Class composition aggregated RECURSIVELY by EQUAL (unweighted) mean:
        leaf = mean of its instances' per-kg totals; parent = mean of its direct
        subclasses' aggregates (each once), NOT a flat pool. {class:{element:amt}}."""
        self._bind_levels()
        tops = self._top_instances()
        if not tops:
            return {}
        elem_classes = sorted({n.cls for n in self.nodes.values()
                               if n.level == "Element"})

        # 1) leaf aggregates: equal mean of each declared class's instances
        leaf_roots = defaultdict(list)
        for root in tops:
            leaf_roots[self.nodes[root].cls].append(root)
        self._check_time_complete(leaf_roots)
        # which level family each ancestor aggregates for (time gating applies
        # to Product/Component families only; Material is time-independent)
        sup = self.superclasses
        anc_family = defaultdict(set)
        for cls_name, roots in leaf_roots.items():
            fam = {self.nodes[r].level for r in roots}
            for a in ancestors_of(sup, cls_name) - {cls_name}:
                anc_family[a] |= fam
        leaf = {}
        for cls_name, roots in leaf_roots.items():
            n = len(roots)
            per_elem = {}
            for ec in elem_classes:
                # element_in_whole(r, ec) IS the per-root sum over that class's
                # element nodes; asking it directly avoids the quadratic probe
                # of every element node against every root.
                acc = sum(self.element_in_whole(r, ec, use) for r in roots)
                amount = acc / n if n else 0.0
                if amount > 0:
                    per_elem[ec] = amount
            leaf[cls_name] = per_elem

        # 2) recursively build ancestor aggregates as the EQUAL mean of the
        # direct subclasses that have an aggregate (leaf or already-built parent).
        out = {c: {e: round(v, 9) for e, v in d.items()} for c, d in leaf.items()}
        # all ancestor classes any instance rolls up to (excluding the leaves)
        ancestors = set()
        for cls_name in leaf_roots:
            ancestors |= (ancestors_of(sup, cls_name) - {cls_name})

        def subclasses_with_agg(parent, computed):
            """Direct subclasses of `parent` that have an aggregate available."""
            return sorted({c for c in (set(leaf) | set(computed))
                           if parent in sup.get(c, ())})

        # resolve parents in order so a parent is built after its subclasses
        computed = {}
        remaining = set(ancestors)
        # iterate to a fixpoint (shallow hierarchies converge in a few passes)
        for _ in range(len(remaining) + 1):
            progressed = False
            for p in sorted(remaining):
                subs = subclasses_with_agg(p, computed)
                # only build when every direct subclass that has instances below
                # it is resolved; here subs are exactly the resolvable ones
                if not subs:
                    continue
                fam_pc = bool(anc_family.get(p, set())
                              & {"Product", "Component"})
                subs = self._parent_gate(p, fam_pc, subs)
                if subs is None:
                    # Product/Component class with neither a time scope nor
                    # the declared year-slice-mean default: NO aggregate key.
                    remaining.discard(p)
                    progressed = True
                    continue
                if not subs:
                    continue        # qualifying subclasses not resolved yet
                per_elem = {}
                for ec in elem_classes:
                    vals = [(_d := (leaf.get(s) or computed.get(s)))[ec]
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

    def aggregate_item_mass(self):
        """Class item mass (kg), PARALLEL to aggregate(): MEASURED instance wins,
        else DERIVED. LEAF = EQUAL mean of own instances' itemMass; PARENT = EQUAL
        mean of direct subclasses' masses over the SAME gate. Kept out of agg()."""
        cache = self._fast()
        key = ("item_mass", self._superclass_fingerprint())
        if key not in cache:
            cache[key] = self._aggregate_item_mass_uncached()
        return cache[key]

    def _aggregate_item_mass_uncached(self):
        self._bind_levels()
        # 1) leaf masses: equal mean of each P/C class's instances that
        #    carry a measured itemMass.
        by_cls = defaultdict(list)
        for n in self.nodes.values():
            if n.level in ("Product", "Component") and n.item_mass is not None:
                by_cls[n.cls].append(n.name)
        leaf = {}
        for cls_name, names in by_cls.items():
            cnt = len(names)
            if not cnt:
                continue
            leaf[cls_name] = sum(self.nodes[r].item_mass for r in names) / cnt

        # 2) recursively build ancestor masses as the EQUAL mean of the direct
        #    subclasses that have a mass, reusing the SAME gate as aggregate().
        out = {c: round(v, 9) for c, v in leaf.items()}
        sup = self.superclasses
        anc_family = defaultdict(set)
        for cls_name in by_cls:
            for a in ancestors_of(sup, cls_name) - {cls_name}:
                anc_family[a] |= {self.nodes[by_cls[cls_name][0]].level}
        ancestors = set()
        for cls_name in by_cls:
            ancestors |= (ancestors_of(sup, cls_name) - {cls_name})

        def subclasses_with_mass(parent, computed):
            return sorted({c for c in (set(leaf) | set(computed))
                           if parent in sup.get(c, ())})

        computed = {}
        remaining = set(ancestors)
        for _ in range(len(remaining) + 1):
            progressed = False
            for p in sorted(remaining):
                subs = subclasses_with_mass(p, computed)
                if not subs:
                    continue
                fam_pc = bool(anc_family.get(p, set()) & {"Product", "Component"})
                subs = self._parent_gate(p, fam_pc, subs)
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

    def _top_instances(self):
        """The top instances: nodes that are a structural whole but never a
        structural part (the products at the head of a chain). These are the
        individuals class-level aggregation pools."""
        self._bind_levels()
        wholes = {s.whole for s in self.stmts if not s.levels_skipped}
        parts = {s.part for s in self.stmts if not s.levels_skipped}
        return sorted(wholes - parts)

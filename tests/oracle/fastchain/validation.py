# (fastchain package — split from the single-file implementation; the
#  arithmetic is verbatim, only the module layout changed.)
"""Structural validation (fan-out, material consistency).
Mixed into SupplyChain (chain.py).
"""
from collections import defaultdict

from .vocab import _vecs_close

class ValidationMixin:
    # ---- structural validation ------------------------------------------
    def check_fanout(self, min_children=2):
        """Every non-leaf whole must have >= `min_children` DIRECT step-wise
        children (real supply chains fan out). Coarse (level-skipping) statements
        do NOT count as a child. Raises AssertionError on violation."""
        self._bind_levels()
        children = defaultdict(set)
        for s in self.stmts:
            if not s.levels_skipped:                 # only structural edges
                children[s.whole].add(s.part)
        # Fan-out applies to P/C wholes only (Material->element exempt). The ONE
        # component exemption is a single-MATERIAL child. A single sub-WHOLE
        # (Component/Product) child is a RED FLAG (thin 1-to-1 chain): a violation.
        def exempt(w, kids):
            return (self.nodes[w].level == "Component" and len(kids) == 1
                    and self.nodes[next(iter(kids))].level == "Material")
        offenders = {w: sorted(c) for w, c in children.items()
                     if self.nodes[w].level in ("Product", "Component")
                     and 0 < len(c) < min_children and not exempt(w, c)}
        # name the single-sub-WHOLE offenders explicitly (the red flag)
        thin = {w: c for w, c in offenders.items()
                if all(self.nodes[p].level in ("Product", "Component") for p in c)}
        assert not offenders, (
            f"scenario {self.id or self.label!r}: these Product/Component wholes "
            f"have fewer than {min_children} structural children (supply chains "
            f"must fan out)"
            + (f" — RED FLAG single-subcomponent/sub-product chains: {thin}"
               if thin else "")
            + f": {offenders}")
        return True

    def material_composition_by_class(self, tol=1e-6):
        """Per material CLASS, the distinct element-fraction vectors across its
        instance NODES (make-up is intrinsic, so every node must match). Returns
        {material_class: [{element_class: fraction}, ...]}; len>1 = inconsistent."""
        self._bind_levels()
        adj = self._adj()

        def vec(mat_node):
            v = defaultdict(float)
            for s in adj.get(mat_node, []):
                if self.nodes[s.part].level == "Element":
                    v[self.nodes[s.part].cls] += s.best_kgkg
            return v

        by_class = defaultdict(list)        # cls -> list of (node, vec)
        for n, nd in self.nodes.items():
            if nd.level == "Material":
                by_class[nd.cls].append((n, vec(n)))

        out = {}
        for cls, items in by_class.items():
            distinct = []                   # list of representative vecs
            for _, v in items:
                if not any(_vecs_close(v, d, tol) for d in distinct):
                    distinct.append(v)
            out[cls] = [dict(d) for d in distinct]
        return out

    def check_material_consistency(self, tol=1e-6):
        """A material class must have ONE intrinsic composition everywhere.
        Raises listing every class whose instances disagree; returns True when
        every material class is consistent."""
        comps = self.material_composition_by_class(tol)
        offenders = {cls: vs for cls, vs in comps.items() if len(vs) > 1}
        assert not offenders, (
            f"scenario {self.id or self.label!r}: these material classes have "
            f"INCONSISTENT compositions (a material must be component-/product-"
            f"independent — same make-up everywhere): "
            + "; ".join(f"{cls} has {len(vs)} distinct compositions" for cls, vs
                        in offenders.items()))
        return True

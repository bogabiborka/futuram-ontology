# (fastchain package — split from the single-file implementation; the
#  arithmetic is verbatim, only the module layout changed.)
"""Material-family aggregation (subClass + MASS-WEIGHTED).
Mixed into SupplyChain (chain.py).
"""
from .hierarchy import ancestors_of

class MaterialFamilyMixin:
    # ---- material-family aggregation (subClass + MASS-WEIGHTED) ----------
    # Unlike P/C (EQUAL subclass mean), a MATERIAL superclass is the MASS-WEIGHTED
    # combination of its leaf materials (min/max preserved); a statement may target a family class directly (member of it + its ancestors).

    def _material_nodes(self):
        """All Material-level node names (whether a leaf material class like
        pureCu or a family class like CuAndCuAlloys)."""
        self._bind_levels()
        return [nm for nm, n in self.nodes.items() if n.level == "Material"]

    def _node_mass(self, node, use="best"):
        """Total mass (kg/kg of the top) of `node` summed over every structural
        path that reaches it from any top instance (path-product of fractions)."""
        adj = self._adj()
        pick = {"best": lambda s: s.best_kgkg, "lo": lambda s: s.lo_kgkg,
                "hi": lambda s: s.hi_kgkg}[use]
        total = [0.0]
        def walk(cur, acc):
            for s in adj[cur]:
                f = acc * pick(s)
                if s.part == node:
                    total[0] += f
                walk(s.part, f)
        for root in self._top_instances():
            walk(root, 1.0)
        return total[0]

    def _material_element_fraction(self, mat_node, element_cls, use="best"):
        """The fraction (kg element / kg material) of `element_cls` in the
        material instance `mat_node`, from its material->element composition
        statements (summed over element nodes of that class)."""
        adj = self._adj()
        pick = {"best": lambda s: s.best_kgkg, "lo": lambda s: s.lo_kgkg,
                "hi": lambda s: s.hi_kgkg}[use]
        frac = 0.0
        for s in adj.get(mat_node, []):
            if self.nodes[s.part].level == "Element" and \
               self.nodes[s.part].cls == element_cls:
                frac += pick(s)
        return frac

    def aggregate_material_family(self, family_cls, element_cls):
        """How much of `element_cls` a `family_cls` material contains, as a
        MASS-WEIGHTED {lo, best, hi} over the leaf-material instances is-a family
        (each weighted by mass present; subClass subsumption), or None if none."""
        self._bind_levels()
        sup = self.superclasses
        members = [m for m in self._material_nodes()
                   if family_cls in ancestors_of(sup, self.nodes[m].cls)]
        if not members:
            return None
        out = {}
        for k in ("lo", "best", "hi"):
            num = 0.0; masssum = 0.0
            for m in members:
                mass = self._node_mass(m, use=k)
                frac = self._material_element_fraction(m, element_cls, use=k)
                num += mass * frac
                masssum += mass
            out[k] = round(num / masssum, 9) if masssum else 0.0
        return out

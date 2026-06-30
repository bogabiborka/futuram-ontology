"""ElementAmountsPlugin — the futuram:Element-content angle: per projected class, one
fq:Amount per named Element class (the deterministic aggregate() best value) + the
unknownElement filler ROW so the level sums to 1.0. DETERMINISTIC ONLY (no lo/hi band).
"""
from __future__ import annotations

from rdflib import Graph

from ..plugin import Plugin
from .. import emit_helpers as E
from ..balance import balance
from ..dedup import dedup
from ..vocab import LEVEL_CLASS, ELEMENT


class ElementAmountsPlugin(Plugin):
    name = "elements"

    def project(self, ctx, upstream) -> Graph:
        g = Graph()
        level_iri = LEVEL_CLASS[ELEMENT]
        for cls_name in ctx.projected_classes():
            per_elem = ctx.agg.get(cls_name) or {}
            # only genuine NAMED Element content (TBox decides the level).
            named = sorted(ec for ec in per_elem
                           if ctx.class_level(ec) == ELEMENT)
            if not named:
                continue
            cls = E.class_node(g, cls_name)
            named_sum = 0.0
            for ec in named:
                best = float(per_elem.get(ec, 0.0))
                named_sum += best
                self.emit_amount(g, ctx, cls, ec, best, level_iri)
            balance(g, cls, named_sum, level_iri)   # tops up with unknown filler row
        dedup(g)                      # valid: one remainder per slot in own output
        return g

    def emit_amount(self, g, ctx, cls, ec, best, level_iri):
        """Emit one element fq:Amount: the deterministic best value only. Relative
        uncertainty is left for UncertaintyRulesetPlugin (DQV-based RSS via PartRelations).
        The poc MC subclass overrides this to attach the lo/hi band."""
        E.amount(g, cls, cls, ec, best, level_class=level_iri)

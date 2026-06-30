"""McPointerPlugin — the Monte-Carlo-provenance angle. An AGGREGATE class defers its
MC interval to pointers (fq:derivedFrom = direct subclasses; fq:derivedFromStatement
= the descendant-leaf statements MC resamples). No-op for a true leaf.
"""
from __future__ import annotations

from rdflib import Graph, Literal

from ..plugin import Plugin
from .. import emit_helpers as E
from ..vocab import FQ, FUT


class McPointerPlugin(Plugin):
    name = "mc_pointers"

    def project(self, ctx, upstream) -> Graph:
        g = Graph()
        for cls_name in ctx.projected_classes():
            self._emit_pointers(g, ctx, E.class_node(g, cls_name), cls_name)
        return g

    @staticmethod
    def _emit_pointers(g, ctx, cls_iri, cls_name):
        """No-op for a leaf; for an aggregate emit mcAvailable + derivedFrom (direct
        subclasses) + derivedFromStatement (descendant-leaf instance statements)."""
        subs = ctx.direct_subclasses(cls_name)
        if not subs:
            return
        g.add((cls_iri, FQ.mcAvailable, Literal(True)))
        for sub in subs:
            g.add((cls_iri, FQ.derivedFrom, FUT[sub]))
        leaves = ctx.descendant_leaf_classes(cls_name)
        if not leaves:
            return
        # the descendant-leaf instance statements MC resamples — pointed at by their
        # content-addressed IRIs read straight off the input composition graph,
        # keyed by the futuram class of each statement's whole node.
        by_wcls = ctx.statement_iris_by_whole_class()
        for leaf in leaves:
            for si in by_wcls.get(leaf, ()):
                g.add((cls_iri, FQ.derivedFromStatement, si))

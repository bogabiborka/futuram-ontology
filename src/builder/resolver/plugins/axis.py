"""AxisPlugin — the aggregation-axis angle: each projected class carries its level edge,
year/period scope, GENERIC slice edges (fq:sliceOf + fq:sliceAxis <strategyIRI>), and
strategy link. No `if time/if drivetrain` — every axis emits through the same loop.
"""
from __future__ import annotations

from rdflib import Graph, RDFS

from ..plugin import Plugin
from .. import emit_helpers as E
from ..vocab import FQ, FUT, LEVEL_CLASS, PRODUCT, COMPONENT, scope_span


class AxisPlugin(Plugin):
    name = "axis"

    def project(self, ctx, upstream) -> Graph:
        g = Graph()
        for cls_name in ctx.projected_classes():
            cls = E.class_node(g, cls_name)
            self._emit_time(g, ctx, cls, cls_name)
        return g

    @staticmethod
    def _emit_time(g, ctx, cls_iri, cls_name, *, level=None):
        from common.vocab import STRATEGY_IRI
        if level is None:
            level = ctx.class_level(cls_name)
        if level is not None:
            g.add((cls_iri, RDFS.subClassOf, LEVEL_CLASS[level]))
        entry = ctx.class_time.get(cls_name)
        if entry is not None:
            E.scope(g, cls_iri, entry)
            # every aggregation axis is ONE generic slice edge: sliceOf <parent>
            # + sliceAxis <the strategy IRI that combines that dimension>, plus a
            # taxonomy subClassOf so the slice still rolls up structurally.
            for parent, axis in entry.get("slices", ()):
                g.add((cls_iri, FQ.sliceOf, FUT[parent]))
                if axis in STRATEGY_IRI:
                    g.add((cls_iri, FQ.sliceAxis, FUT[STRATEGY_IRI[axis]]))
                g.add((cls_iri, RDFS.subClassOf, FUT[parent]))
            if entry.get("strategy"):
                g.add((cls_iri, FQ.aggregationStrategy, FUT[STRATEGY_IRI[entry["strategy"]]]))
            return
        if level in (PRODUCT, COMPONENT):
            slices = ctx.slices_of_base(cls_name)
            if slices:
                spans = [scope_span(e) for e in slices]
                y0, y1 = min(s for s, _ in spans), max(e for _, e in spans)
                derived = {"year": y0} if y0 == y1 else {"start": y0, "end": y1}
                E.scope(g, cls_iri, derived)
                g.add((cls_iri, FQ.aggregationStrategy, FUT.YearSliceMeanStrategy))

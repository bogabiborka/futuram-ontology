"""ItemMassPlugin — the absolute-kg reference anchor: fq:itemMass on each
Product/Component class (derived per class by the strategy; measured instance wins).
"""
from __future__ import annotations

from rdflib import Graph, Literal, XSD

from ..plugin import Plugin
from .. import emit_helpers as E
from ..vocab import FQ


class ItemMassPlugin(Plugin):
    name = "item_mass"

    def project(self, ctx, upstream) -> Graph:
        g = Graph()
        for cls_name in ctx.projected_classes():
            kg = ctx.item_mass.get(cls_name)
            if kg is None:
                continue
            cls = E.class_node(g, cls_name)
            g.add((cls, FQ.itemMass, Literal(float(kg), datatype=XSD.double)))
        return g

"""TaxonomyPlugin — re-emit the declared part-hierarchy into the served view.

The frozen futuram-hierarchy (ontology/tbox/futuram-hierarchy.ttl) declares the
multi-level key hierarchy in all three dimensions: a part is `rdfs:subClassOf` its
parent group (elvEmbeddedElectronicsCables -> elvEmbeddedElectronics ->
Component; AlAndAlAlloys -> non-ferrousMetals -> Material; …). A parent group is
NOT directly composed — it is the (mass-conserving) roll-up of its subclasses,
gated by its AggregationStrategy ("either a CompositionStatement OR a strategy").

AxisPlugin emits only the LEVEL edge (… subClassOf Component/Material/Product) and
the SLICE edges (year/drivetrain); it drops the intermediate part->parent edge. So
without this plugin the served /query graph flattens elvEmbeddedElectronicsCables
straight onto Component, the parent group has no part-subclasses to roll up, and it
gets no in-vehicle scope node — Q7's "embedded electronics" group is unanswerable as
a single served class.

This plugin restores the intermediate `rdfs:subClassOf` edges, for product,
component AND material classes uniformly, from `ctx.direct_declared_superclasses`.
The roll-up ARITHMETIC already lives in aggregate.py (it walks idx.superclasses);
the only thing missing was the taxonomy edge in the VIEW. (The unknown-remainder
holders' own `subClassOf <kind>` edges — unknown_in_<parent> -> unknownComponent /
unknownMaterial / … — are emitted by PartOfPlugin where the holder is minted, so the
"total unknown at a level" query works on the served graph without this plugin
touching them.)

It iterates the SERVED class set read from the upstream structural plugins — NOT
ctx.projected_classes() alone — exactly like LabelPlugin: a material/component group
(CuAndCuAlloys, elvEmbeddedElectronics) is served as an `fq:contains` subject or
constituent yet absent from projected_classes(), so iterating only the latter would
miss precisely the parent groups this plugin exists to connect. An edge is emitted
only when BOTH the child and its declared parent are actually served — never a
dangling edge to a non-served or catch-all class (catch-all source keys never enter
the declared hierarchy; they fold into unknown* in the ETL).
"""
from __future__ import annotations

from rdflib import Graph, RDF, RDFS, OWL

from ..plugin import Plugin
from .. import emit_helpers as E
from ..vocab import FUT, FQ


class TaxonomyPlugin(Plugin):
    name = "taxonomy"
    # read what the structural plugins served so we can connect the parent groups
    # they emitted (which are absent from ctx.projected_classes()).
    deps = ("elements", "component", "partof", "axis", "item_mass")

    def project(self, ctx, upstream: Graph) -> Graph:
        g = Graph()
        served = self._served_classes(ctx, upstream)
        # Walk the declared chain UP from every served class, emitting each
        # parent->child edge along the way. We do NOT require the parent to be
        # independently served: an intermediate family class (e.g. AlAndAlAlloys)
        # may have NO aggregate of its own when its leaves appear only as
        # constituents (not as wholes with sub-composition) — yet the navigation
        # edge child rdfs:subClassOf <family> is exactly what lets a query group
        # the served leaf constituents by their declared family and roll up toward
        # an ancestor that IS served (… -> non-ferrousMetals -> Material). Such a
        # pure-navigation parent (subClassOf edges, no fq:contains) is already a
        # normal served shape. Emitting transitively connects the leaves all the
        # way to the served root; we never emit an edge to a level root (AxisPlugin
        # owns those) nor to a catch-all (those never enter the declared hierarchy).
        seen = set()
        frontier = list(served)
        while frontier:
            cls_name = frontier.pop()
            for parent in ctx.direct_declared_superclasses(cls_name):
                child_iri = E.class_node(g, cls_name)
                parent_iri = E.class_node(g, parent)
                g.add((child_iri, RDFS.subClassOf, parent_iri))
                if parent not in seen:
                    seen.add(parent)
                    frontier.append(parent)
        return g

    @staticmethod
    def _served_classes(ctx, upstream):
        """Every futuram class the served graph actually carries — owl:Class
        subjects, fq:constituent objects, and the projected aggregates. Same set
        LabelPlugin labels, so taxonomy and labels cover identical classes."""
        fut = str(FUT)

        def local(s):
            return str(s)[len(fut):] if str(s).startswith(fut) else None

        served = {local(s) for s in upstream.subjects(RDF.type, OWL.Class)}
        served |= {local(s) for s in upstream.objects(None, FQ.constituent)}
        served |= set(ctx.projected_classes())
        served.discard(None)
        return served

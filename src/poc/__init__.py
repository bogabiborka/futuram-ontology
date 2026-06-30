# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml"]
# ///
"""poc — proof-of-concept live layer that EXTENDS the builder (poc -> builder). The
core builder is deterministic best-value and NEVER imports poc; this adds the Monte-
Carlo band on TOP (aggregate_mc, McElementAmountsPlugin, POC_PLUGINS, resolve_all_mc).
"""
from rdflib import Graph

from builder.resolver import Resolver, DEFAULT_PLUGINS, ElementAmountsPlugin
from builder.resolver import emit_helpers as E
from builder.resolver.vocab import K_LO, K_HI, local

from .aggregate_mc import aggregate_mc


def _mc_for(ctx):
    """The MC aggregate for this run's index, computed once and cached on the
    ctx's generic cache (an optimisation; recomputable from ctx.index)."""
    got = ctx._cache.get("poc_mc")
    if got is None:
        got = ctx._cache["poc_mc"] = aggregate_mc(ctx.index)
    return got


class McElementAmountsPlugin(ElementAmountsPlugin):
    """The element-content plugin WITH the Monte-Carlo band. Reuses the deterministic
    plugin and only overrides emit_amount to attach the MC 5/95 lo/hi spread (clamped
    around best); a true AGGREGATE class defers its band, serving the central value."""

    def emit_amount(self, g, ctx, cls, ec, best, level_iri):
        cls_name = local(cls)
        is_aggregate = bool(ctx.direct_subclasses(cls_name))
        mc_e = None if is_aggregate else _mc_for(ctx).get(cls_name, {}).get(ec)
        lo = mc_e[K_LO] if mc_e else None
        hi = mc_e[K_HI] if mc_e else None
        if lo is not None and best < lo:
            lo = best
        if hi is not None and best > hi:
            hi = best
        E.amount(g, cls, cls, ec, best, level_class=level_iri,
                 lo=lo, hi=hi, with_dist=True)


# the poc pipeline: the builder defaults with the MC element plugin swapped in for
# the deterministic one (same name "elements", so PartOf's deps still resolve).
POC_PLUGINS = [McElementAmountsPlugin() if p.name == "elements" else p
               for p in DEFAULT_PLUGINS]


def resolve_all_mc(source, into=None, *, only=None):
    """Run the poc pipeline (MC band included) over a composition RDF graph (or
    anything with .to_graph()). RDF graph in -> served fq: graph out, with the
    amountLow/High band on non-aggregate element amounts."""
    return Resolver(POC_PLUGINS).run(source, into=into, only=only)

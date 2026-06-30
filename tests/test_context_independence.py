# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "pytest"]
# ///
"""Context-independence invariant: ResolverContext is a PURE cache, never a source
of truth — a plugin given a fresh ctx must produce IDENTICAL RDF. Enforced per
scenario via pristine-vs-prewarmed ctx and two independent Resolver.run() calls.
"""
import pytest
from rdflib.compare import isomorphic

import scenarios
from builder.resolver import DEFAULT_PLUGINS, Resolver
from builder.resolver.context import ResolverContext

SIDS = sorted(scenarios.ALL)


def _prewarm(ctx):
    """Force every derived/cached attribute on the context so its caches are
    fully populated BEFORE the plugin runs (the 'populated ctx' arm)."""
    _ = ctx.index
    _ = ctx.superclasses
    _ = ctx.agg
    _ = ctx.item_mass
    _ = ctx.element_classes
    ctx.structural_adj()
    ctx.component_nodes()
    ctx.statement_iris_by_whole_class()


def _run_plugin(plugin, comp_graph, *, prewarm):
    """Run one plugin's project() against a freshly-built ctx, optionally
    pre-warming the ctx caches first. Feeds the plugin its deps' output (built
    from the same fresh ctx) so a dep-having plugin still gets valid upstream."""
    ctx = ResolverContext(comp_graph)
    if prewarm:
        _prewarm(ctx)
    from rdflib import Graph
    by_name = {p.name: p for p in DEFAULT_PLUGINS}
    upstream = Graph()
    for d in plugin.deps:
        upstream += by_name[d].project(ResolverContext(comp_graph), Graph())
    return plugin.project(ctx, upstream)


@pytest.mark.parametrize("sid", SIDS)
def test_each_plugin_independent_of_ctx_state(sid):
    """Every plugin's output is identical whether the ctx was pristine or fully
    pre-warmed — ctx is a cache, not a source of truth."""
    comp = scenarios.ALL[sid].to_graph()
    for plugin in DEFAULT_PLUGINS:
        pristine = _run_plugin(plugin, comp, prewarm=False)
        warmed = _run_plugin(plugin, comp, prewarm=True)
        assert isomorphic(pristine, warmed), (
            f"{sid}: plugin {plugin.name!r} output depends on ctx cache state "
            f"(pristine {len(pristine)} triples vs pre-warmed {len(warmed)})")


@pytest.mark.parametrize("sid", SIDS)
def test_pipeline_run_is_reproducible_from_graph(sid):
    """Two independent runs over the same input graph (each a fresh ctx) are
    isomorphic — no hidden cross-run state, the graph fully determines output."""
    comp = scenarios.ALL[sid].to_graph()
    a = Resolver(DEFAULT_PLUGINS).run(comp)
    b = Resolver(DEFAULT_PLUGINS).run(comp)
    assert isomorphic(a, b), f"{sid}: pipeline output not reproducible from graph"

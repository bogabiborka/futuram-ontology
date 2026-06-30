"""resolver.engine — the Resolver: run a DAG of plugins into one fq: graph.
Knows NOTHING about any plugin: topologically sorts by declared `deps` (cycle-detected),
runs each handing it the MERGED output of its deps, and unions every fresh output in.
"""
from __future__ import annotations

from rdflib import Graph

from .context import ResolverContext


class Resolver:
    """Runs a plugin DAG. Construct with the plugin list (e.g. DEFAULT_PLUGINS);
    call run(sc, ...) to produce the served graph."""

    def __init__(self, plugins):
        self._plugins = list(plugins)
        self._by_name = {}
        for p in self._plugins:
            if not p.name:
                raise ValueError(f"plugin {p!r} has no name")
            if p.name in self._by_name:
                raise ValueError(f"duplicate plugin name {p.name!r}")
            self._by_name[p.name] = p
        self._order = self._toposort()

    # ---- DAG ordering --------------------------------------------------------
    def _toposort(self):
        """Plugins in dependency order (a plugin runs after all its deps). Raises
        on an unknown dep or a cycle."""
        for p in self._plugins:
            for d in p.deps:
                if d not in self._by_name:
                    raise ValueError(
                        f"plugin {p.name!r} depends on unknown plugin {d!r}")
        order, state = [], {}      # state: 0=visiting, 1=done
        def visit(p):
            s = state.get(p.name)
            if s == 1:
                return
            if s == 0:
                raise ValueError(f"plugin dependency cycle through {p.name!r}")
            state[p.name] = 0
            for d in p.deps:
                visit(self._by_name[d])
            state[p.name] = 1
            order.append(p)
        for p in self._plugins:    # stable: declaration order breaks ties
            visit(p)
        return order

    # ---- run -----------------------------------------------------------------
    def run(self, sc, *, into=None, only=None, tbox=None):
        """Project `sc` into a served fq: graph in DAG order. `into` accumulates (returned);
        `only` = class local-names to emit (None = all); `tbox` injects a TBox.
        Deterministic best-value only — NO Monte-Carlo (the poc layer adds the MC band)."""
        ctx = ResolverContext(sc, graph=into, only=only, tbox=tbox)
        produced: dict = {}        # plugin name -> its output Graph
        for p in self._order:
            upstream = Graph()
            for d in p.deps:
                upstream += produced[d]
            out = p.project(ctx, upstream)
            produced[p.name] = out if out is not None else Graph()
            ctx.graph += produced[p.name]
        return ctx.graph

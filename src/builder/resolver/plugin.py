"""resolver.plugin — the plugin contract. A PLUGIN produces one angle of the fq:
projection: a PURE function returning a FRESH Graph of only its own triples, already
VALID (balanced + deduped). It reads a dep's output by declaring it in `deps`.
"""
from __future__ import annotations

import abc

from rdflib import Graph


class Plugin(abc.ABC):
    """One node of the projection DAG. Pure: project() returns a new Graph and reads
    only `ctx` (shared compute-once state) and `upstream` (its deps' merged output);
    no shared mutable graph, so a plugin is testable in isolation."""

    #: stable identifier — the key other plugins name in their `deps`.
    name: str = ""

    #: names of the plugins whose output this one needs to read (DAG edges).
    deps: tuple = ()

    @abc.abstractmethod
    def project(self, ctx, upstream: Graph) -> Graph:
        """Produce this plugin's triples (a NEW Graph; valid, balanced, deduped). `ctx`
        is the ResolverContext (scope + compute-once reads); `upstream` is the read-only
        merged output of this plugin's `deps` (empty when none) — do NOT mutate it."""
        raise NotImplementedError

    def __repr__(self):
        d = f" deps={list(self.deps)}" if self.deps else ""
        return f"<{type(self).__name__} name={self.name!r}{d}>"

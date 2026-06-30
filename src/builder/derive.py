# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "owlrl", "pyshacl"]
# ///
"""builder.derive — ONE global fq: graph from composition-statement RDF (no dir/CSV).
All sources pool into ONE merged graph, aggregate ONCE per class, project to ONE served
graph (merge_sources then derive_all — the full derive that store.add_source matches).
"""
from rdflib import Graph, URIRef, RDFS, Literal

from common import pipeline
from common.vocab import EX
from . import resolver
from .slicer import (YearSlicer, ValueAxisSlicer, attach_value_strategy,
                     DRIVETRAIN_AXIS)


def _served_graph(comp_graph, *, only=None):
    """Resolve a composition RDF graph into the served fq: graph + attach the fq:
    TBox (resolver reads the composition graph directly, no Chain). Deterministic
    best-value only — the deployed .ttl carries no MC band (that's poc/live)."""
    g = Graph()
    resolver.resolve_all(comp_graph, into=g, only=only)
    g.parse(str(pipeline.QUERY_TBOX), format="turtle")   # fq: TBox terms
    g.parse(str(pipeline.UNCERTAINTY_TBOX), format="turtle")  # uncertainty ruleset (rule travels with data)
    # Class rdfs:labels arrive through resolve_all above (resolver's LabelPlugin
    # derives every projected class's label generically) — nothing to copy here.
    return g


# to_graph instance IRIs live in the EX namespace and are REUSED across scenario
# files, so a naive merge collapses different cars into one. Namespacing them per
# source keeps instances distinct so the CLASS (elvBEV) pools them correctly.
_EX = str(EX)


def _namespaced(src_graph, source_id, into=None):
    """Copy src_graph with every ex: INSTANCE IRI prefixed by the source id so instances
    never collide; class IRIs (futuram:*) and TBox terms stay shared. `into` adds the
    remapped triples straight into an existing graph instead of a throwaway copy."""
    out = into if into is not None else Graph()
    def remap(term):
        if isinstance(term, URIRef) and str(term).startswith(_EX):
            tail = str(term)[len(_EX):]
            return URIRef(f"{_EX}{source_id}/{tail}")
        return term
    for s, p, o in src_graph:
        # from_graph keys nodes by rdfs:label, so namespace the labels of
        # INSTANCE individuals too (an ex: subject) — otherwise two "carA"
        # labels collapse. Class labels (futuram: subjects) stay shared.
        if (p == RDFS.label and isinstance(s, URIRef)
                and str(s).startswith(_EX) and isinstance(o, Literal)):
            o = Literal(f"{source_id}/{o}", lang=o.language)
        out.add((remap(s), p, remap(o)))
    return out


# The drivetrain slice axis is a VALUE axis handled GENERICALLY by
# ValueAxisSlicer(DRIVETRAIN_AXIS): a component shared across >= 2 drivetrain values is
# retyped per value (DrivetrainMeanStrategy). The values are read from the GRAPH.


def _finalise_store(merged):
    """Idempotent store-finalisation: run the generic axis slicers YEAR-first then
    drivetrain value axis (the value slicer's sliceOf edges only exist after year mints
    the slice classes). Mutates `merged` in place (splices rewrite back) and returns."""
    merged += YearSlicer().derive(merged)
    sliced = ValueAxisSlicer(DRIVETRAIN_AXIS).apply(merged)
    merged.remove((None, None, None))
    merged += sliced
    attach_value_strategy(merged, DRIVETRAIN_AXIS)
    # IDENTITY pass: stamp futuram:contentHash on every PartRelation (skolemize blank
    # relations). Runs HERE — after slicing, before any consumer (store dedup,
    # mc_pointers) reads identity. Authoring (ETL) never stamps identity; this is it.
    from .relation_identity import stamp_identity
    stamp_identity(merged)
    return merged


def merge_sources(pairs):
    """Pool composition sources (an iterable of (source_id, graph) from the ETL) into ONE
    finalised store graph: namespace each source's instances, union, then finalise
    (slicers derive slice classes from graph markers). Returns (merged_graph, n_sources)."""
    merged = Graph()
    n = 0
    for source_id, g in pairs:
        _namespaced(g, str(source_id), into=merged)
        n += 1
    _finalise_store(merged)
    return merged, n


def derive_all(merged):
    """Derive the served fq: graph from a finalised merged graph: one aggregate per
    CLASS over ALL pooled instances — the ONE-SHOT full derive that incremental
    add_source must match. Best value only, NO Monte-Carlo. Returns the served Graph."""
    return _served_graph(merged)


def store_stats(merged):
    """Reporting stats over a merged composition graph (the same graph the resolver
    reads): instance + class counts. Pure RDF; for CLI/build summaries."""
    from .index import build_index
    from . import aggregate as _A
    idx = build_index(merged, sid="futuram_global")
    return {
        "instances": len(_A.top_instances(idx)),
        "classes": len(_A.aggregate(idx)),
    }

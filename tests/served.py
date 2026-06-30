# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "owlrl", "pyyaml"]
# ///
"""served — a small TEST helper for querying the virtual fq: graph.

A resolve-then-query convenience over `builder.resolver` (not deployed). As test
code it may import both builder and etl (the no-cross-dep rule is src/-only).
"""
import pathlib

from rdflib import Graph, RDF, OWL

from common import pipeline
from common.vocab import FQ
from builder import resolver


def _load_composition(ttl_path):
    """Parse a composition Turtle file into one rdflib.Graph, merging its sibling
    year-invariant material->element file when present (buckets split the chemistry
    into pipeline.EM_SHARED_NAME). RDF in -> RDF out; no Chain."""
    path = pathlib.Path(ttl_path)
    g = Graph()
    g.parse(str(path), format="turtle")
    shared = path.parent / pipeline.EM_SHARED_NAME
    if path.name != pipeline.EM_SHARED_NAME and shared.exists():
        g.parse(str(shared), format="turtle")
    return g


class _Row(dict):
    """A query result row: dict or attribute access by SPARQL variable name
    (row["v"] / row.v). Values are raw rdflib terms (Literal/URIRef); tests call
    float(...) / str(...) on them as needed."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def to_new_model_graph(chain):
    """Invert the frozen oracle's OLD time-based-classes RDF into the NEW model the
    BUILDER consumes (base-typed instances + referenceYear/period, slices derived).
    Pure graph->graph (oracle untouched); yields what the ETL emitter does.
    """
    from rdflib import RDFS, Literal
    from common.vocab import FUT, TIME
    from rdflib.namespace import XSD

    g = chain.to_graph()

    # slice classes = anything that declares it is a slice OF something.
    slice_classes = set(g.subjects(FUT.sliceOf, None))

    # the time scope each slice class carries (to move onto its instances).
    def _scope_terms(cls):
        y = g.value(cls, FUT.referenceYear)
        if y is not None:
            return [(FUT.referenceYear, y)]
        per = g.value(cls, FUT.hasReferencePeriod)
        if per is not None:
            return [(FUT.hasReferencePeriod, per)]   # keep the whole interval node
        return []

    out = Graph()
    for pfx, ns in g.namespaces():
        out.bind(pfx, ns)

    for s, p, o in g:
        # an instance typed by a slice class -> retype onto the timeless base +
        # carry the slice's time scope onto the instance.
        if p == RDF.type and o in slice_classes:
            base = g.value(o, FUT.sliceOf)
            # walk to the TIMELESS base (a slice may sliceOf another slice).
            while base in slice_classes:
                base = g.value(base, FUT.sliceOf)
            out.add((s, RDF.type, base))
            for tp, tv in _scope_terms(o):
                out.add((s, tp, tv))
            continue
        # drop the slice class's own definitional triples (the builder re-derives).
        if s in slice_classes and p in (FUT.sliceOf, FUT.sliceAxis,
                                        FUT.referenceYear, FUT.hasReferencePeriod,
                                        FUT.hasAggregationStrategy, RDFS.subClassOf,
                                        RDF.type, RDFS.label):
            continue
        out.add((s, p, o))
    return out


def served_graph(chain, *, with_mc=False):
    """Project a composition source into the served fq: graph + fq:/uncertainty
    TBoxes (a scenario CHAIN is first inverted to the new model; a Graph passes
    through). with_mc=False = deterministic (best only); True = poc adds MC band."""
    source = chain if isinstance(chain, Graph) else to_new_model_graph(chain)
    # Slice at the pooling step (derive._finalise_store runs the axis slicers, as
    # derive.derive_all does before resolve_all) so the resolver sees sliced graph.
    from builder import derive as _derive
    source = _derive._finalise_store(source)
    g = Graph()
    if with_mc:
        import poc
        poc.resolve_all_mc(source, into=g)
    else:
        resolver.resolve_all(source, into=g)
    g.parse(str(pipeline.QUERY_TBOX), format="turtle")
    g.parse(str(pipeline.UNCERTAINTY_TBOX), format="turtle")   # uncertainty ruleset
    return g


def _rows(graph, sparql):
    rows = []
    for binding in graph.query(sparql):
        row = _Row()
        for var in binding.labels:
            row[str(var)] = binding[var]
        rows.append(row)
    return rows


def query(chain_or_graph, sparql, *, with_mc=False):
    """Run a SPARQL SELECT against the served fq: graph; return _Row list. Accepts
    a Chain/SupplyChain (its served graph is built) or an already-built Graph."""
    g = chain_or_graph if isinstance(chain_or_graph, Graph) else \
        served_graph(chain_or_graph, with_mc=with_mc)
    return _rows(g, sparql)


def save(chain, path, *, with_mc=False, format="turtle"):
    """Persist the served fq: query graph (the virtual ontology) to disk."""
    served_graph(chain, with_mc=with_mc).serialize(destination=str(path),
                                                   format=format)
    return path


def every_contains_subject_is_a_class(obj):
    """True iff every fq:contains subject in the served graph is typed owl:Class
    (the fq: view is class-only; used by test A4). Accepts a Graph, an Endpoint
    (anything with .served_graph()), or a chain."""
    if isinstance(obj, Graph):
        g = obj
    elif hasattr(obj, "served_graph"):
        g = obj.served_graph()
    else:
        g = served_graph(obj)
    for subj in set(g.subjects(FQ.contains, None)):
        if (subj, RDF.type, OWL.Class) not in g:
            return False
    return True


class Endpoint:
    """Thin per-chain query convenience for tests: holds one chain, lazily builds
    its served fq: graph and answers SPARQL. `materialize_all` is a no-op (eager
    vs lazy projection is identical — exactly what test_D1 asserts)."""

    def __init__(self, chain, *, materialize_all=False, with_mc=False):
        self.chain = chain
        self._with_mc = with_mc
        self._served = served_graph(chain, with_mc=with_mc) if materialize_all else None

    def _graph(self):
        if self._served is None:
            self._served = served_graph(self.chain, with_mc=self._with_mc)
        return self._served

    def served_graph(self):
        return self._graph()

    def query(self, sparql):
        return _rows(self._graph(), sparql)

    def save(self, path, format="turtle"):
        self._graph().serialize(destination=str(path), format=format)
        return path


class _RoutedEndpoint:
    """Common machinery: route a query to the relevant composition-RDF files,
    read each back to a chain (from_turtle), serve it, union the served graphs
    (+ fq: TBox), and answer SPARQL. The served projection per file is cached."""

    def __init__(self, router):
        self.router = router
        self._cache = {}            # file path -> served fq: Graph

    def _served_for_file(self, ttl_path):
        key = str(ttl_path)
        if key not in self._cache:
            comp = _load_composition(ttl_path)
            self._cache[key] = served_graph(comp)
        return self._cache[key]

    def _union(self, files):
        g = Graph()
        for f in files:
            g += self._served_for_file(f)
        g.parse(str(pipeline.QUERY_TBOX), format="turtle")
        return g

    def query(self, sparql, years=None, classes=None):
        files = self.router.files_for(years=years, classes=classes)
        return _rows(self._union(files), sparql)

    def routed_files(self, years=None, classes=None):
        """Which files a query would load (for inspection/explainability)."""
        return self.router.files_for(years=years, classes=classes)


class BucketedEndpoint(_RoutedEndpoint):
    """Serve a dataset chunked into year buckets, loading ONLY the bucket files a
    query needs (etl.buckets.BucketRouter)."""

    def __init__(self, catalog_path):
        from etl import buckets
        super().__init__(buckets.BucketRouter(catalog_path))


class CorpusEndpoint(_RoutedEndpoint):
    """Serve a DIRECTORY of composition RDF files, routing each query to only the
    files that can answer it (etl.corpus.CorpusRouter)."""

    def __init__(self, catalog_path):
        from etl import corpus
        super().__init__(corpus.CorpusRouter(catalog_path))

# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl", "openpyxl"]
# ///
"""project_corpus — project a directory of baseline composition RDF into the query-optimized fq: dataset (TTL), one
fq: .ttl per composition .ttl, so the QUERY dataset is committed TTL Fuseki loads on
startup (no projection at `docker compose up`).
"""
import pathlib
import sys


from rdflib import Graph

from builder import resolver
from common import pipeline
from . import corpus


def _load_composition(comp_ttl):
    """Parse a composition Turtle file into one rdflib.Graph, merging its sibling
    year-invariant material->element file (pipeline.EM_SHARED_NAME) when present so
    the union is the complete composition graph. Files without a sibling load as-is."""
    path = pathlib.Path(comp_ttl)
    g = Graph()
    g.parse(str(path), format="turtle")
    shared = path.parent / pipeline.EM_SHARED_NAME
    if path.name != pipeline.EM_SHARED_NAME and shared.exists():
        g.parse(str(shared), format="turtle")
    return g


def project_file(comp_ttl, out_ttl):
    """One composition RDF file -> its served fq: graph (Turtle), by calling the
    resolver directly with the composition graph (RDF in -> RDF out)."""
    comp = _load_composition(comp_ttl)
    g = Graph()
    resolver.resolve_all(comp, into=g)
    g.parse(str(pipeline.QUERY_TBOX), format="turtle")   # fq: TBox terms
    g.serialize(destination=str(out_ttl), format="turtle")
    return len(g)


def project_corpus(comp_dir, query_dir):
    """Project every composition .ttl in comp_dir to a served fq: .ttl in
    query_dir (same basename). Skips TBox/shape files."""
    comp_dir = pathlib.Path(comp_dir)
    query_dir = pathlib.Path(query_dir)
    query_dir.mkdir(parents=True, exist_ok=True)
    done = []
    for f in sorted(comp_dir.glob("*.ttl")):
        if f.name in corpus._SKIP_NAMES or f.name.endswith("-served.ttl"):
            continue
        out = query_dir / f.name
        n = project_file(f, out)
        done.append((f.name, n))
    return done


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("comp_dir", type=pathlib.Path, help="baseline composition RDF dir")
    ap.add_argument("query_dir", type=pathlib.Path, help="output fq: dir")
    args = ap.parse_args(argv)
    done = project_corpus(args.comp_dir, args.query_dir)
    total = sum(n for _, n in done)
    print(f"projected {len(done)} files -> {args.query_dir} ({total} fq: triples)")
    for name, n in done:
        print(f"  {name}: {n} triples")


if __name__ == "__main__":
    main()

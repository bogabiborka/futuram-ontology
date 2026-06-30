# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "owlrl", "pyshacl"]
# ///
"""etl.serve_corpus — on-disk PIPELINE RUNNER: scan a baseline composition RDF directory, feed
graphs to the pure-RDF builder (derive_all), write the query-optimized fq: dataset. serve_corpus
= one-shot full derive; serve_corpus_incrementally = additive (value-identical) path.
"""
import pathlib
import sys


from etl import corpus
from builder import derive, store
try:
    from common.memcap import memory_guard
except Exception:                       # common not importable -> no cap (correct)
    import contextlib

    @contextlib.contextmanager
    def memory_guard(*a, **k):
        yield


def serve_corpus(comp_dir, out_ttl):
    """Scan comp_dir, pool all sources into one merged composition graph, have the
    BUILDER derive the served fq: graph, write it to out_ttl. The ONE-SHOT full derive
    (source of truth for the incremental path), best value only, NO Monte-Carlo."""
    with memory_guard(label="serve_corpus"):     # hard cap; abort before machine OOM
        merged, n_sources = derive.merge_sources(corpus.load_corpus(comp_dir))
        served = derive.derive_all(merged)

        out_ttl = pathlib.Path(out_ttl)
        out_ttl.parent.mkdir(parents=True, exist_ok=True)
        served.serialize(destination=str(out_ttl), format="turtle")

        stats = derive.store_stats(merged)
    return {
        "files_merged": n_sources,
        "instances": stats["instances"],
        "classes": stats["classes"],
        "fq_triples": len(served),
        "out": str(out_ttl),
    }


def serve_corpus_incrementally(comp_dir, out_ttl):
    """Serve comp_dir INCREMENTALLY: one builder.store.add_source per composition file
    into a fresh persistent view. Value-identical to one-shot serve_corpus but each
    source re-derives only its affected fq-classes."""
    with memory_guard(label="serve_corpus_incrementally"):
        return store.add_sources(out_ttl, corpus.load_corpus(comp_dir))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("comp_dir", type=pathlib.Path)
    ap.add_argument("-o", "--out", type=pathlib.Path, required=True,
                    help="output single fq: .ttl")
    ap.add_argument("--incremental", action="store_true",
                    help="serve via the additive add_source sequence (one add per "
                         "composition file) instead of one-shot — value-identical, "
                         "but re-derives only each file's affected fq-classes")
    args = ap.parse_args(argv)
    if args.incremental:
        info = serve_corpus_incrementally(args.comp_dir, args.out)
        print(f"added {info['files_added']} composition files incrementally -> "
              f"{info['fq_triples']} fq: triples")
        print(f"wrote {info['out']}")
    else:
        info = serve_corpus(args.comp_dir, args.out)
        print(f"merged {info['files_merged']} composition files -> "
              f"{info['instances']} instances, {info['classes']} classes")
        print(f"wrote {info['out']}  ({info['fq_triples']} fq: triples)")


if __name__ == "__main__":
    main()

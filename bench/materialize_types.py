"""Materialize the rdf:type subclass closure for the composition dataset.

The composition TDB2 store has NO reasoner, so a leaf-typed instance is not asserted
to its base (Product/Component/Material/Element). Emit the entailed ancestor rdf:type
triples (ceiling = direct subclasses of Constituent) to a regenerable loader file.
"""
import argparse
import sys
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS

# The shared upper class every constituent kind sits under; the named base
# classes are its direct futuram subclasses (see gen_void_composition.py). The
# closure ceiling is exactly those base classes — discovered, not hardcoded.
CONSTITUENT_ROOT = URIRef("http://w3id.org/CEON/ontology/resourceODP/Constituent")
FUT = "https://www.purl.org/futuram#"


def _base_classes(g: Graph) -> set:
    return {c for c in g.subjects(RDFS.subClassOf, CONSTITUENT_ROOT)
            if isinstance(c, URIRef) and str(c).startswith(FUT)}


def _ancestors(parents: dict, cls, ceiling: set, cache: dict) -> set:
    """rdfs:subClassOf* ancestors of cls (excluding cls), stopping AT (and
    including) any class in `ceiling` — never ascending past a base class into
    the upper ontology. Memoised."""
    if cls in cache:
        return cache[cls]
    cache[cls] = set()                      # guard against cycles
    acc: set = set()
    for parent in parents.get(cls, ()):
        acc.add(parent)
        if parent not in ceiling:           # stop climbing once we hit a base
            acc |= _ancestors(parents, parent, ceiling, cache)
    cache[cls] = acc
    return acc


def materialize(g: Graph) -> Graph:
    """Return a fresh Graph holding ONLY the entailed rdf:type triples (one per
    individual × ancestor class up to a named base) not already asserted in g."""
    parents: dict = {}
    for sub, sup in g.subject_objects(RDFS.subClassOf):
        if isinstance(sub, URIRef) and isinstance(sup, URIRef):
            parents.setdefault(sub, set()).add(sup)

    ceiling = _base_classes(g)
    cache: dict = {}
    out = Graph()
    for s, c in g.subject_objects(RDF.type):
        if not isinstance(c, URIRef):
            continue
        for anc in _ancestors(parents, c, ceiling, cache):
            if (s, RDF.type, anc) not in g:
                out.add((s, RDF.type, anc))
    return out


def load_dirs(dirs, exclude: Path | None = None) -> Graph:
    """Parse every *.ttl under each dir into one graph. Pass BOTH the clean
    composition statements AND the bench TBox augmentation: the subclass closure
    is computed over their union, but the clean dir is never written to."""
    g = Graph()
    skip = exclude.resolve() if exclude else None
    files = []
    for d in dirs:
        files += [f for f in sorted(Path(d).rglob("*.ttl"))
                  if skip is None or f.resolve() != skip]
    if not files:
        sys.exit(f"no *.ttl found under {', '.join(str(d) for d in dirs)}")
    for f in files:
        g.parse(f, format="turtle")
    print(f"# loaded {len(files)} files, {len(g)} triples from "
          f"{', '.join(str(d) for d in dirs)}", file=sys.stderr)
    return g


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comp_dirs", type=Path, nargs="+",
                    help="composition data dir(s): the clean statements dir AND "
                         "the bench TBox dir (the closure spans their union)")
    ap.add_argument("-o", "--output", type=Path, required=True,
                    help="where to write the entailed rdf:type triples (.ttl) — "
                         "a bench-only file, never the clean composition dir")
    args = ap.parse_args()

    g = load_dirs(args.comp_dirs, exclude=args.output)
    entailed = materialize(g)
    args.output.write_text(entailed.serialize(format="turtle"))
    print(f"# wrote {len(entailed)} entailed rdf:type triples -> {args.output}",
          file=sys.stderr)


if __name__ == "__main__":
    main()

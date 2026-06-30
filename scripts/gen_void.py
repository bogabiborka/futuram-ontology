# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///
"""Generate a VoID(+ext) description (the schema the sparql-llm indexer feeds the
LLM) of a served /query graph by INTROSPECTION only. Slice classes (detected from
data) collapse into one merged chapter; with no -o, deterministic Turtle to stdout.

Run with uv (auto-installs rdflib):
    uv run scripts/gen_void.py fuseki/futuram/data/query  -o sparql-llm/futuram_void.ttl
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

VOID = Namespace("http://rdfs.org/ns/void#")
VOIDX = Namespace("http://ldf.fi/void-ext#")
FQ = Namespace("https://www.purl.org/futuram/query#")

# A class is "time/slice-based" iff it is the SUBJECT of one of these predicates.
# Detected from the data — we never hardcode the slice class IRIs themselves.
SLICE_PREDICATES = {
    FQ.sliceOf,            # the generic slice edge (any axis)
    FQ.sliceAxis,
    FQ.referenceYear,
    FQ.periodStart,
    FQ.periodEnd,
}

# Prefixes the served vocabularies use, so the emitted Turtle is readable.
PREFIXES = {
    "owl": str(OWL),
    "rdf": str(RDF),
    "rdfs": str(RDFS),
    "xsd": str(XSD),
    "void": str(VOID),
    "void-ext": str(VOIDX),
    "prov": "http://www.w3.org/ns/prov#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "mw": "https://purl.org/metalwheel#",
    "crit": "http://purl.org/futuram/criticality#",
    "fq": "https://www.purl.org/futuram/query#",
    "futuram": "https://www.purl.org/futuram#",
    "chebi": "http://purl.obolibrary.org/obo/",
}


def load_query_graph(query_dirs) -> Graph:
    """Parse every *.ttl under each dir into one graph — the served union. Accepts
    one dir or several (e.g. the clean query dir plus a bench overlay dir)."""
    if isinstance(query_dirs, (str, Path)):
        query_dirs = [query_dirs]
    g = Graph()
    files = []
    for d in query_dirs:
        files += sorted(Path(d).rglob("*.ttl"))
    if not files:
        sys.exit(f"no *.ttl found under {', '.join(str(d) for d in query_dirs)}")
    for f in files:
        g.parse(f, format="turtle")
    print(f"# loaded {len(files)} files, {len(g)} triples from "
          f"{', '.join(str(d) for d in query_dirs)}", file=sys.stderr)
    return g


def types_of(g: Graph, s) -> set:
    """rdf:type(s) of a subject; bare resources used as classes -> owl:Class."""
    ts = set(g.objects(s, RDF.type))
    if not ts:
        # Untyped subject. If it is used as a class (has subClassOf, or is the
        # object of rdf:type / a class slot), describe it under owl:Class — the
        # same fallback the hand-written VoID used for its `owl:Class` block.
        if (s, RDFS.subClassOf, None) in g or (None, RDF.type, s) in g:
            return {OWL.Class}
        return set()
    return ts


def slice_classes(g: Graph) -> set:
    """The time/slice-based classes: subjects of any SLICE_PREDICATE."""
    out = set()
    for pred in SLICE_PREDICATES:
        out.update(g.subjects(pred, None))
    return out


def _new_slot():
    return {"datatypes": set(), "classes": set()}


def _accumulate(slot, o, stypes):
    """Fold one object value into a partition slot (datatype or class)."""
    if isinstance(o, Literal):
        slot["datatypes"].add(o.datatype or XSD.string)  # plain/lang -> xsd:string
    else:
        o_types = stypes(o)
        slot["classes"].update(o_types if o_types else {OWL.Class})


def build_void(g: Graph) -> Graph:
    """Introspect g and return its VoID(+ext) description as a fresh Graph.
    Time/slice classes fold into ONE merged chapter, everything else is
    described per rdf:type; no class or property IRI is hardcoded."""
    type_cache = {}

    def stypes(s):
        if s not in type_cache:
            type_cache[s] = types_of(g, s)
        return type_cache[s]

    sliced = slice_classes(g)

    # subject-type -> predicate -> slot, for NON-sliced subjects.
    partitions = defaultdict(lambda: defaultdict(_new_slot))
    # one merged slot map for ALL sliced classes (the special chapter).
    slice_part = defaultdict(_new_slot)

    for s, p, o in g:
        if p == RDF.type:
            continue  # rdf:type is not described as a partition predicate
        if s in sliced:
            _accumulate(slice_part[p], o, stypes)
            continue
        s_types = stypes(s)
        for st in s_types:
            # A sliced class appearing only as someone's TYPE is part of the
            # chapter, not its own block.
            if st in sliced:
                continue
            _accumulate(partitions[st][p], o, stypes)

    out = Graph()
    for pfx, ns in PREFIXES.items():
        out.bind(pfx, Namespace(ns))

    def emit_block(subject_node, slots):
        for p in sorted(slots, key=str):
            slot = slots[p]
            pp = BNode()
            out.add((subject_node, VOID.propertyPartition, pp))
            out.add((pp, VOID.property, p))
            for dt in sorted(slot["datatypes"], key=str):
                dp = BNode()
                out.add((pp, VOIDX.datatypePartition, dp))
                out.add((dp, VOIDX.datatype, dt))
            for cls in sorted(slot["classes"], key=str):
                cp = BNode()
                out.add((pp, VOID.classPartition, cp))
                out.add((cp, VOID["class"], cls))

    # Normal per-type blocks.
    for st in sorted(partitions, key=str):
        node = BNode()
        out.add((node, VOID["class"], st))
        emit_block(node, partitions[st])

    # The CRITICALITY chapter: the criticality vocabulary (crit:remark /
    # crit:importance / crit:year and the importance individuals) hangs off ChEBI
    # element classes and a blank remark node — the remark node has no rdf:type, so
    # those predicates never enter a normal per-type block and the guards would not
    # know them. Describe the whole crit: namespace generically in ONE block (every
    # crit:-namespaced predicate used anywhere in the graph, with its object
    # classes/individuals), so the class/predicate guards recognise crit:* as REAL
    # and the schema discovery surfaces it. No term is hardcoded — only the
    # namespace IRI (already in PREFIXES) selects what to fold in.
    crit_ns = PREFIXES["crit"]
    crit_part = defaultdict(_new_slot)
    for s, p, o in g:
        if str(p).startswith(crit_ns):
            _accumulate(crit_part[p], o, stypes)
    if crit_part:
        node = BNode()
        out.add((node, VOID["class"], OWL.Class))
        out.add((node, RDFS.comment, Literal(
            "Criticality overlay: an element's ChEBI class may carry a criticality "
            "flag via crit:remark [ crit:importance ?imp ; crit:year ?y ], where "
            "?imp is crit:CRITICAL or crit:STRATEGIC. To find critical raw "
            "materials, navigate the flag — ?chebi crit:remark/crit:importance "
            "crit:CRITICAL — NOT a 'CriticalRawMaterial' class (there is none). "
            "Reach an element's ChEBI class with ?element rdfs:subClassOf ?chebi.")))
        emit_block(node, crit_part)

    # The special time-based chapter: one block for ALL slice classes, tagged so
    # the LLM knows it summarizes many classes (not a single one).
    if slice_part:
        node = BNode()
        out.add((node, VOID["class"], OWL.Class))
        out.add((node, RDFS.comment, Literal(
            "Aggregation-slice classes (e.g. "
            "futuram:<drivetrain>_<component>_Y<year>): one summarized partition "
            "for ALL such classes. Each is a slice of a parent class along an "
            "aggregation axis via fq:sliceOf <parent> + fq:sliceAxis <strategy> "
            "(year and/or drivetrain), carrying fq:referenceYear or "
            "fq:periodStart..fq:periodEnd. Query any one like a normal class "
            "(fq:contains ...); pick the slice by its year/drivetrain.")))
        out.add((node, VOID.entities, Literal(len(sliced))))
        emit_block(node, slice_part)

    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query_dir", type=Path, nargs="+",
                    help="query data dir(s) loaded as one graph (clean dir, plus "
                         "an optional bench overlay dir)")
    ap.add_argument("-o", "--output", type=Path,
                    help="output .ttl (default: stdout)")
    args = ap.parse_args()

    data = load_query_graph(args.query_dir)
    void = build_void(data)
    ttl = void.serialize(format="turtle")
    if args.output:
        args.output.write_text(ttl)
        print(f"# wrote {len(void)} triples -> {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(ttl)


if __name__ == "__main__":
    main()

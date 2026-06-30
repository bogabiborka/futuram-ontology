# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///
"""Generate a VoID (+ VoID-ext) description of a /composition graph.

The COMPOSITION counterpart to scripts/gen_void.py: introspection-only, plus
blank-node typing and a unit inventory. -t/--tbox folds in label/comment.

Run with uv (auto-installs rdflib):
    uv run bench/gen_void_composition.py fuseki/futuram/data/composition \
        -o bench/futuram_void_composition.ttl
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

VOID = Namespace("http://rdfs.org/ns/void#")
VOIDX = Namespace("http://ldf.fi/void-ext#")
FUT = Namespace("https://www.purl.org/futuram#")
QUDT = Namespace("http://qudt.org/schema/qudt/")

# Prefixes the composition vocabulary uses, so the emitted Turtle is readable.
PREFIXES = {
    "owl": str(OWL),
    "rdf": str(RDF),
    "rdfs": str(RDFS),
    "xsd": str(XSD),
    "void": str(VOID),
    "void-ext": str(VOIDX),
    "futuram": str(FUT),
    "qudt": str(QUDT),
    "unit": "http://qudt.org/vocab/unit/",
    "ceonq": "http://w3id.org/CEON/ontology/quantity/",
    "ceonr": "http://w3id.org/CEON/ontology/resourceODP/",
    "ceonst": "http://w3id.org/CEON/ontology/statement/",
    "dqv": "http://www.w3.org/ns/dqv#",
    "prov": "http://www.w3.org/ns/prov#",
    "time": "http://www.w3.org/2006/time#",
    "ex": "https://www.purl.org/futuram/example#",
    "crit": "http://purl.org/futuram/criticality#",
}

# qudt:unit is the predicate whose objects we additionally want LISTED (not just
# typed) — the unit inventory the aggregator must reconcile. Detected by IRI of
# the predicate, not by hardcoding the unit values themselves.
UNIT_PRED = QUDT.unit

# The shared upper class every constituent kind sits under. The NAMED base
# classes (Product/Component/Material/Element) are exactly the futuram classes
# DIRECT rdfs:subClassOf this root — discovered structurally, never hardcoded.
CONSTITUENT_ROOT = URIRef("http://w3id.org/CEON/ontology/resourceODP/Constituent")


# Scrub CONCRETE examples out of folded TBox comments (keep the definitional
# prose). Matched by SHAPE — an "e.g." parenthetical, a "~N unit" value — so no
# concrete dataset string leaks.
_EG_CLAUSE = re.compile(r"\s*\(e\.g\.[^)]*\)")          # "(e.g. … )" parentheticals
_APPROX_NUM = re.compile(r"~\s*[\d.]+\s*[A-Za-z/%]+")     # "~1450 kg", "~20%"


def scrub_comment(text: str) -> str:
    """Remove concrete dataset examples from a folded TBox comment, keeping the
    definitional prose. Shape-based ('e.g.' parenthetical, '~N unit' value), not a
    denylist; schema words the prose names (refersTo, etc.) are left intact."""
    text = _EG_CLAUSE.sub("", text)
    text = _APPROX_NUM.sub("a value", text)
    text = re.sub(r"\s+([.;,])", r"\1", text)         # tidy leftover spacing
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def load_graph(comp_dirs) -> Graph:
    """Parse every *.ttl under each dir into one graph — the served composition.
    Mirrors the Fuseki entrypoint (bulk-loads every nested *.ttl). Pass several
    dirs to describe the SERVED union (clean statements + bench overlay)."""
    if isinstance(comp_dirs, (str, Path)):
        comp_dirs = [comp_dirs]
    g = Graph()
    files = []
    for d in comp_dirs:
        files += sorted(Path(d).rglob("*.ttl"))
    if not files:
        sys.exit(f"no *.ttl found under {', '.join(str(d) for d in comp_dirs)}")
    for f in files:
        g.parse(f, format="turtle")
    print(f"# loaded {len(files)} files, {len(g)} triples from "
          f"{', '.join(str(d) for d in comp_dirs)}", file=sys.stderr)
    return g


def types_of(g: Graph, s) -> set:
    """rdf:type(s) of a subject/object (blank nodes included, so a typed bnode
    chains the property partitions through the reified structure). An untyped
    resource used as a class falls back to owl:Class (like scripts/gen_void.py)."""
    ts = set(g.objects(s, RDF.type))
    if ts:
        return ts
    if isinstance(s, BNode):
        return set()                       # untyped blank: structural, skip
    if (s, RDFS.subClassOf, None) in g or (None, RDF.type, s) in g:
        return {OWL.Class}
    return set()


def _new_slot():
    return {"datatypes": set(), "classes": set(), "units": set()}


def _accumulate(slot, p, o, stypes):
    """Fold one object value into a partition slot. qudt:unit objects are ALSO
    recorded verbatim (the unit inventory), beyond their type partition."""
    if isinstance(o, Literal):
        slot["datatypes"].add(o.datatype or XSD.string)
    else:
        o_types = stypes(o)
        slot["classes"].update(o_types if o_types else {OWL.Class})
        if p == UNIT_PRED:
            slot["units"].add(o)


# The reified-structure predicates: a class is STRUCTURAL (own VoID block) iff its
# instances are subjects of one (else collapsed into the chapter); detected by
# predicate IRI. hasCompositionStatement is ABSENT (its subject is a domain whole).
CEONST = "http://w3id.org/CEON/ontology/statement/"
STRUCTURAL_PREDS = {
    FUT.hasPartRelation, FUT.refersTo,
    FUT.hasQuantity, FUT.hasBestValue,
    FUT.hasDistribution, FUT.numericValue, QUDT.numericValue,
    URIRef("http://www.w3.org/ns/dqv#isMeasurementOf"),
    URIRef("http://w3id.org/CEON/ontology/quantity/hasMinimalValueIncludedOfInterval"),
    URIRef("http://w3id.org/CEON/ontology/quantity/hasMaximalValueIncludedOfInterval"),
}


def _base_classes(g: Graph) -> set:
    """The NAMED base classes (Product/Component/Material/Element), discovered as
    the futuram classes DIRECT rdfs:subClassOf CONSTITUENT_ROOT (never hardcoded).
    Each gets its own void:class block with the instance-level predicates."""
    return {c for c in g.subjects(RDFS.subClassOf, CONSTITUENT_ROOT)
            if isinstance(c, URIRef) and str(c).startswith(str(FUT))}


# Predicates describing ONTOLOGY AXIOMS (TBox meta-structure), excluded from the
# constituent chapter so they don't bloat/mislead it. rdfs:subClassOf and
# rdfs:label (navigation) are KEPT; matched by IRI, never by value.
_ONTOLOGY_AXIOM_PREDS = {
    OWL.onProperty, OWL.someValuesFrom, OWL.allValuesFrom, OWL.hasValue,
    OWL.unionOf, OWL.intersectionOf, OWL.complementOf, OWL.oneOf,
    OWL.propertyChainAxiom, OWL.inverseOf, OWL.equivalentClass,
    OWL.disjointWith, OWL.members, OWL.cardinality, OWL.minCardinality,
    OWL.maxCardinality, OWL.qualifiedCardinality,
    RDFS.domain, RDFS.range, RDFS.subPropertyOf, RDFS.seeAlso, RDFS.isDefinedBy,
}


def _is_ontology_axiom_pred(p) -> bool:
    return p in _ONTOLOGY_AXIOM_PREDS


# Pure ONTOLOGY-PLUMBING classes the loaded TBox carries (property declarations,
# restriction bnodes, ontology header, …): not queryable data, so no VoID block.
# owl:Class is NOT here — it is the synthetic key for the constituent chapter.
_META_CLASSES = {
    OWL.AnnotationProperty, OWL.DatatypeProperty, OWL.ObjectProperty,
    OWL.TransitiveProperty, OWL.SymmetricProperty, OWL.FunctionalProperty,
    OWL.InverseFunctionalProperty, OWL.NamedIndividual, OWL.Restriction,
    OWL.AllDisjointClasses, OWL.AllDifferent, OWL.Ontology, RDF.Property,
    RDFS.Class, RDFS.Datatype,
}


def _is_meta_class(c) -> bool:
    return c in _META_CLASSES


def _subclasses_of(g: Graph, base) -> set:
    """All classes rdfs:subClassOf* base (the base included), via the data's
    subClassOf closure. Used to roll a typed instance up to its named base."""
    seen = {base}
    frontier = [base]
    while frontier:
        cur = frontier.pop()
        for sub in g.subjects(RDFS.subClassOf, cur):
            if sub not in seen:
                seen.add(sub)
                frontier.append(sub)
    return seen


def _domain_classes(g: Graph) -> set:
    """The constituent CLASSES (domain vocabulary): typed onto an instance (not
    statement machinery) and rolling up to a named base via rdfs:subClassOf* (which
    keeps ontology-support classes out). Structural types and bases keep own blocks."""
    structural_types = set()
    for pred in STRUCTURAL_PREDS:
        for s in g.subjects(pred, None):
            structural_types.update(g.objects(s, RDF.type))
    bases = _base_classes(g)
    constituents = set().union(*(_subclasses_of(g, b) for b in bases)) if bases else set()
    out = set()
    for o in g.objects(None, RDF.type):
        if (isinstance(o, URIRef) and o not in structural_types
                and o not in bases and o in constituents):
            out.add(o)
    return out


def _navigation_facts(g: Graph, domain: set) -> dict:
    """Derive from the data HOW a named class connects to its statements, so the
    VoID states the real navigation path. Returns flags (whole_is_instance,
    type_links_classes, subclass_chain, *_have_labels)."""
    wholes = set(g.subjects(FUT.hasCompositionStatement, None))
    parts = set(g.objects(None, FUT.refersTo))
    individuals = wholes | parts
    whole_is_instance = any(
        isinstance(w, URIRef) and (w, RDF.type, None) in g for w in individuals)
    # an instance typed into a domain class?
    type_links_classes = any(
        o in domain for s in individuals for o in g.objects(s, RDF.type))
    # do those (or any domain) classes have a subClassOf parent?
    subclass_chain = any((c, RDFS.subClassOf, None) in g for c in domain)
    # Do the domain CLASSES carry rdfs:label, or only instances? If classes have
    # none, a term must be matched against the class IRI local-name (or via an
    # instance's label -> its rdf:type). Detected, never assumed.
    classes_with_label = sum(1 for c in domain if (c, RDFS.label, None) in g)
    instances_with_label = 0
    for s in set(g.subjects(RDF.type, None)):
        if (s, RDFS.label, None) in g and any(t in domain for t in g.objects(s, RDF.type)):
            instances_with_label += 1
    classes_have_labels = classes_with_label > 0
    instances_have_labels = instances_with_label > 0
    return {
        "whole_is_instance": whole_is_instance,
        "type_links_classes": type_links_classes,
        "subclass_chain": subclass_chain,
        "classes_have_labels": classes_have_labels,
        "instances_have_labels": instances_have_labels,
    }


def build_void(g: Graph, tbox: Graph | None = None) -> Graph:
    """Introspect g and return its VoID(+ext) description as a fresh Graph: one
    block per rdf:type, a property partition per predicate, the qudt:unit inventory,
    and the constituent CLASSES folded into ONE chapter. No IRI hardcoded."""
    type_cache = {}

    def stypes(s):
        if s not in type_cache:
            type_cache[s] = types_of(g, s)
        return type_cache[s]

    domain = _domain_classes(g)
    bases = _base_classes(g)

    # base class -> set of its subclasses (incl. itself), so an instance typed
    # into any leaf can be rolled up to the base(s) it belongs to. Computed from
    # the data's rdfs:subClassOf closure — no class name is assumed.
    base_members = {b: _subclasses_of(g, b) for b in bases}

    def bases_of(s_types):
        """The named base classes an instance (with these rdf:types) rolls up
        to, via rdfs:subClassOf*."""
        return {b for b, members in base_members.items()
                if any(t in members for t in s_types)}

    # subject-type -> predicate -> slot, for STRUCTURAL (non-domain) subjects.
    partitions = defaultdict(lambda: defaultdict(_new_slot))
    # one merged slot map for ALL domain (constituent) classes (the chapter).
    domain_part = defaultdict(_new_slot)
    # named base class -> predicate -> slot: the instance-level predicates the
    # members of each base actually carry (e.g. itemMass on Product/Component).
    base_part = defaultdict(lambda: defaultdict(_new_slot))
    # CONCRETE domain class -> predicate -> slot: the instance-level predicates an
    # instance of THIS exact class carries, so a validator typing a subject directly
    # as the concrete class confirms the predicate without the subClassOf* roll-up.
    concrete_part = defaultdict(lambda: defaultdict(_new_slot))
    for s, p, o in g:
        if p == RDF.type:
            continue
        if s in domain:
            # The domain CLASS node itself: keep only navigation/aggregation
            # predicates (subClassOf chain, label, strategy); drop the TBox
            # ONTOLOGY-AXIOM vocab the loaded hierarchy carries (meta-structure).
            if _is_ontology_axiom_pred(p):
                continue
            _accumulate(domain_part[p], p, o, stypes)
            continue
        s_types = stypes(s)
        # An INSTANCE typed into a domain class carries instance-level facts
        # (itemMass, label) — fold into the chapter AND attribute to the base
        # class(es) it rolls up to, so Product/Component get a real block.
        if any(st in domain or st in bases for st in s_types):
            _accumulate(domain_part[p], p, o, stypes)
            for b in bases_of(s_types):
                _accumulate(base_part[b][p], p, o, stypes)
            # also attribute to each CONCRETE domain class this instance is typed
            # as (skip the abstract bases — they get their own base_part block).
            if not _is_ontology_axiom_pred(p):
                for st in s_types:
                    if st in domain and st not in bases:
                        _accumulate(concrete_part[st][p], p, o, stypes)
        for st in s_types:
            if st in domain or st in bases or _is_meta_class(st):
                continue
            _accumulate(partitions[st][p], p, o, stypes)

    out = Graph()
    for pfx, ns in PREFIXES.items():
        out.bind(pfx, Namespace(ns))

    # Pull label/comment for each described class & predicate out of the TBox so
    # the LLM sees the human meaning (hasCompositionStatement / hasPartRelation /
    # refersTo, kg/kg …).
    def describe(node, iri):
        if tbox is None:
            return
        for lbl in tbox.objects(iri, RDFS.label):
            out.add((node, RDFS.label, lbl))
        for cmt in tbox.objects(iri, RDFS.comment):
            out.add((node, RDFS.comment, Literal(scrub_comment(str(cmt)))))

    def emit_block(subject_node, slots):
        for p in sorted(slots, key=str):
            slot = slots[p]
            pp = BNode()
            out.add((subject_node, VOID.propertyPartition, pp))
            out.add((pp, VOID.property, p))
            describe(pp, p)
            for dt in sorted(slot["datatypes"], key=str):
                dp = BNode()
                out.add((pp, VOIDX.datatypePartition, dp))
                out.add((dp, VOIDX.datatype, dt))
            for cls in sorted(slot["classes"], key=str):
                cp = BNode()
                out.add((pp, VOID.classPartition, cp))
                out.add((cp, VOID["class"], cls))
            # The explicit unit inventory: every distinct qudt:unit observed.
            for u in sorted(slot["units"], key=str):
                up = BNode()
                out.add((pp, VOIDX.datatypePartition, up))
                out.add((up, VOIDX.datatype, u))

    for st in sorted(partitions, key=str):
        node = BNode()
        out.add((node, VOID["class"], st))
        describe(node, st)
        emit_block(node, partitions[st])

    # The named base classes: one void:class block each carrying the instance-level
    # predicates their members have (so itemMass lands on Product/Component), so a
    # validator can type an instance up to its base via subClassOf* and confirm it.
    for b in sorted(base_part, key=str):
        node = BNode()
        out.add((node, VOID["class"], b))
        describe(node, b)
        out.add((node, RDFS.comment, Literal(
            "A named base class. Its individuals are typed into a subclass and "
            "reached via ?inst rdf:type/rdfs:subClassOf* <thisClass>. The "
            "property partitions below are the predicates those individuals "
            "carry.")))
        emit_block(node, base_part[b])

    # Concrete domain classes whose instances carry instance-level predicates: one
    # void:class block each, so a query typing a subject directly as the concrete
    # class validates against it (predicate-less ones stay folded in the chapter).
    for st in sorted(concrete_part, key=str):
        node = BNode()
        out.add((node, VOID["class"], st))
        describe(node, st)
        out.add((node, RDFS.comment, Literal(
            "A concrete constituent class with individuals. The property "
            "partitions are the predicates an instance of this class carries "
            "(?inst a <thisClass> ; <pred> …); a class-level number is an "
            "AGGREGATE over those instances.")))
        emit_block(node, concrete_part[st])

    # The constituent-classes chapter: one block summarising ALL domain classes,
    # whose COMMENT states the real navigation path from _navigation_facts (a base
    # class is reached from a whole/part INSTANCE via rdf:type + rdfs:subClassOf*).
    if domain_part:
        nav = _navigation_facts(g, domain)
        node = BNode()
        out.add((node, VOID["class"], OWL.Class))

        lines = [
            "Constituent classes (the domain vocabulary): every "
            "Product/Component/Material/Element class and its per-year slice. "
            "One summarised partition for ALL such classes."
        ]
        # Only assert a navigation step if the data actually exhibits it.
        if nav["whole_is_instance"]:
            lines.append(
                "IMPORTANT: a statement's whole (subject of "
                "futuram:hasCompositionStatement) and its parts (object of "
                "futuram:refersTo on each futuram:PartRelation) are INSTANCES "
                "(individuals), NOT these classes. A class name is therefore NOT "
                "itself a whole or part — do not filter them by a class-name "
                "string; reach the class via the instance's rdf:type.")
        # Resolution advice, driven by where labels actually are in THIS data.
        if not nav["classes_have_labels"]:
            lines.append(
                "NOTE: these CLASSES carry NO rdfs:label in this dataset. "
                "Use get_skill(\"resolve-class\") to resolve a plain-language "
                "term to a class IRI.")
            if nav["instances_have_labels"]:
                lines.append(
                    "Only INSTANCES carry rdfs:label here; if you match an "
                    "instance by label, take its rdf:type to get the class.")
        else:
            lines.append(
                "Resolve a plain-language term to a class IRI via rdfs:label on "
                "the class.")
        if nav["type_links_classes"]:
            lines.append(
                "Each instance is linked to its class by rdf:type "
                "(?instance a ?class).")
        if nav["subclass_chain"]:
            lines.append(
                "Classes chain to coarser, user-named base classes via "
                "rdfs:subClassOf (a per-year slice rdfs:subClassOf its base). So "
                "to go from a class a user NAMES to its statements: find the "
                "instances of it or its subclasses with  ?inst "
                "rdf:type/rdfs:subClassOf* <thatClass> , then traverse  ?inst "
                "futuram:hasCompositionStatement ?stmt . ?stmt "
                "futuram:hasPartRelation ?pr . ?pr futuram:refersTo ?part ; "
                "futuram:hasQuantity ?q .  A single class-level number is an "
                "AGGREGATE over those instances (this endpoint does not store it; "
                "you must compute it).")
        out.add((node, RDFS.comment, Literal(" ".join(lines))))
        out.add((node, VOID.entities, Literal(len(domain))))

        # Emit an explicit rdf:type property partition (instance -> class): the
        # navigation edge the previous VoID omitted. classes = domain classes
        # instances are typed into (sampled + capped; count in void:entities).
        if nav["type_links_classes"]:
            type_targets = set()
            for w in g.subjects(FUT.hasCompositionStatement, None):
                for o in g.objects(w, RDF.type):
                    if o in domain:
                        type_targets.add(o)
            for w in g.objects(None, FUT.refersTo):
                for o in g.objects(w, RDF.type):
                    if o in domain:
                        type_targets.add(o)
            pp = BNode()
            out.add((node, VOID.propertyPartition, pp))
            out.add((pp, VOID.property, RDF.type))
            out.add((pp, RDFS.comment, Literal(
                "instance -> its constituent class (the edge that connects a "
                "whole/part individual to the class a user names)")))
            for cls in sorted(type_targets, key=str)[:40]:
                cp = BNode()
                out.add((pp, VOID.classPartition, cp))
                out.add((cp, VOID["class"], cls))

        emit_block(node, domain_part)

    # The CRITICALITY chapter: the criticality vocabulary (crit:remark /
    # crit:importance / crit:year + the importance individuals) hangs off ChEBI
    # element classes via a typeless blank remark node, so it never enters a normal
    # per-type block. Describe the whole crit: namespace generically in ONE block so
    # the guards recognise crit:* as REAL and schema discovery surfaces it. Mirrors
    # scripts/gen_void.py. No term hardcoded — only the namespace IRI selects it.
    crit_ns = PREFIXES["crit"]
    crit_part = defaultdict(_new_slot)
    for s, p, o in g:
        if str(p).startswith(crit_ns):
            _accumulate(crit_part[p], p, o, stypes)
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

    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comp_dir", type=Path, nargs="+",
                    help="composition source dir(s) describing the served graph — "
                         "e.g. the clean statements dir AND the bench overlay")
    ap.add_argument("-o", "--output", type=Path,
                    help="output .ttl (default: stdout)")
    ap.add_argument("-t", "--tbox", type=Path, default=None,
                    help="composition-statement TBox to fold in label/comment "
                         "from (e.g. ontology/tbox/composition-statement.ttl)")
    args = ap.parse_args()

    data = load_graph(args.comp_dir)
    tbox = None
    if args.tbox:
        tbox = Graph()
        tbox.parse(str(args.tbox), format="turtle")
        print(f"# folded TBox labels/comments from {args.tbox} ({len(tbox)} triples)",
              file=sys.stderr)
    void = build_void(data, tbox)
    ttl = void.serialize(format="turtle")
    if args.output:
        args.output.write_text(ttl)
        print(f"# wrote {len(void)} triples -> {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(ttl)


if __name__ == "__main__":
    main()

# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///
"""Extract the FutuRaM Product/Component/Material/Element taxonomy into a
self-contained Turtle file (owl:Class decls, internal rdfs:subClassOf, rdfs:label).
Both reasoned core.owl and the hand-written TBox are loaded so the tree is complete.

Run with uv (auto-installs rdflib via the inline metadata above):
    uv run extract_hierarchy.py [OUTPUT.ttl]
"""
import sys

from rdflib import Graph, Namespace, URIRef, RDF, RDFS, OWL
from rdflib.namespace import NamespaceManager

FUTURAM = Namespace("https://www.purl.org/futuram#")
QUDT = Namespace("http://qudt.org/schema/qudt/")
UNIT = Namespace("http://qudt.org/vocab/unit/")

# Domain-taxonomy module layered on the composition-statement TBox (which
# defines the four roots + futuram properties). We declare our own ontology IRI
# and owl:imports that module so the dependency is explicit in RDF.
HIERARCHY_ONTOLOGY = URIRef("https://www.purl.org/futuram/hierarchy")
COMPOSITION_STATEMENT_ONTOLOGY = URIRef(
    "https://www.purl.org/futuram/composition-statement"
)

SOURCES = [
    ("futuram-ontology/core.owl", "xml"),
    ("futuram-ontology/dataset2rdf-converter/futuram_tbox.ttl", "turtle"),
]
OUTPUT = sys.argv[1] if len(sys.argv) > 1 else "futuram-hierarchy.ttl"

ROOTS = [FUTURAM.Product, FUTURAM.Component, FUTURAM.Material, FUTURAM.Element]

# Optional class-level ITEM MASS (kg): typical mass of one item of a
# Product/Component class. Informational only (no role in composition); only
# countable Product/Component carry it. Entry: class local-name -> kg.
ITEM_MASS_KG = {
    "V0301030105":                  1450.0,   # one large BEV car
    "wiringHarness":                3.0,
    "elvElectricMotor":             8.0,
    "elvEmbeddedElectronicsCables": 1.6,
    "elvEVbattery":                 330.0,
}


def in_futuram(term):
    return isinstance(term, URIRef) and str(term).startswith(str(FUTURAM))


def main():
    src = Graph()
    for path, fmt in SOURCES:
        try:
            src.parse(path, format=fmt)
        except FileNotFoundError:
            print(f"warning: {path} not found, skipping", file=sys.stderr)

    # All futuram-namespace classes: anything declared owl:Class or used as a
    # subject/object of a futuram subClassOf edge.
    classes = set()
    for s in src.subjects(RDF.type, OWL.Class):
        if in_futuram(s):
            classes.add(s)

    # Internal subClassOf edges (child -> parent, both in futuram namespace).
    parents = {}
    for child, parent in src.subject_objects(RDFS.subClassOf):
        if in_futuram(child) and in_futuram(parent):
            classes.add(child)
            classes.add(parent)
            parents.setdefault(child, set()).add(parent)
    classes.update(ROOTS)

    # Keep classes whose ancestor chain reaches one of the four roots.
    root_set = set(ROOTS)

    def reaches_root(c, seen=None):
        seen = seen or set()
        if c in root_set:
            return True
        if c in seen:
            return False
        seen.add(c)
        return any(reaches_root(p, seen) for p in parents.get(c, ()))

    kept = {c for c in classes if c in root_set or reaches_root(c)}

    # Build the output graph: declarations + internal subClassOf + label only.
    out = Graph()
    out.namespace_manager = NamespaceManager(Graph())
    out.bind("futuram", FUTURAM)
    out.bind("owl", OWL)
    out.bind("rdfs", RDFS)
    out.bind("qudt", QUDT)
    out.bind("unit", UNIT)

    from rdflib import BNode, Literal
    from rdflib.namespace import XSD

    # Ontology header: this module imports the composition-statement TBox (which
    # defines the four roots + futuram properties), so the roots are NOT
    # re-declared here — only referenced as subClassOf targets of their children.
    out.add((HIERARCHY_ONTOLOGY, RDF.type, OWL.Ontology))
    out.add((HIERARCHY_ONTOLOGY, OWL.imports, COMPOSITION_STATEMENT_ONTOLOGY))
    out.add((HIERARCHY_ONTOLOGY, RDFS.label,
             Literal("FutuRaM Product / Component / Material / Element hierarchy")))

    for c in kept:
        if c in root_set:
            # defined in the imported composition-statement ontology; do not
            # re-declare here. Children still reference it via subClassOf.
            continue
        out.add((c, RDF.type, OWL.Class))
        for p in parents.get(c, ()):
            if p in kept:
                out.add((c, RDFS.subClassOf, p))
        label = src.value(c, RDFS.label)
        if label is not None:
            out.add((c, RDFS.label, label))
        # optional class-level item mass (kg), Product/Component only
        kg = ITEM_MASS_KG.get(str(c).split("#")[-1])
        if kg is not None:
            qv = BNode()
            out.add((qv, RDF.type, QUDT.QuantityValue))
            out.add((qv, QUDT.numericValue, Literal(float(kg), datatype=XSD.double)))
            out.add((qv, QUDT.unit, UNIT.KiloGM))
            out.add((c, FUTURAM.itemMass, qv))

    header = (
        "#################################################################\n"
        "#\n"
        "#    FutuRaM Product / Component / Material / Element hierarchy\n"
        "#\n"
        "#    Class-based taxonomy extracted from the FutuRaM ontology.\n"
        "#    Contains only owl:Class declarations, their rdfs:subClassOf\n"
        "#    links within the futuram namespace, and rdfs:label. No\n"
        "#    individuals, properties or external parents.\n"
        "#\n"
        "#    owl:imports the composition-statement TBox, which defines the\n"
        "#    four roots (Product/Component/Material/Element) and the futuram\n"
        "#    properties; the roots are not re-declared here.\n"
        "#\n"
        "#    Generated by extract_hierarchy.py (uv run + rdflib).\n"
        "#\n"
        "#################################################################\n\n"
    )
    body = out.serialize(format="turtle")
    with open(OUTPUT, "w") as f:
        f.write(header + body)

    print(f"Loaded {len(src)} triples; wrote {len(kept)} classes "
          f"({len(out)} triples) to {OUTPUT}")


if __name__ == "__main__":
    main()

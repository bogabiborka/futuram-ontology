# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///
"""Generate a realistic copper-in-a-car A-Box (sample-instances.ttl): cars fan
out into components into materials, copper reaching the car on many paths.
Grouped shape; mixed units; one car-B path left incomplete.

Run:  uv run gen_sample.py
"""
from rdflib import Graph, Namespace, Literal, BNode, RDF, RDFS, XSD

FUT     = Namespace("https://www.purl.org/futuram#")
EX      = Namespace("https://www.purl.org/futuram/example#")
DQV     = Namespace("http://www.w3.org/ns/dqv#")
QUDT    = Namespace("http://qudt.org/schema/qudt/")
UNIT    = Namespace("http://qudt.org/vocab/unit/")
TIME    = Namespace("http://www.w3.org/2006/time#")
CEONQ   = Namespace("http://w3id.org/CEON/ontology/quantity/")
PROV    = Namespace("http://www.w3.org/ns/prov#")
DCTERMS = Namespace("http://purl.org/dc/terms/")
CEONPO  = Namespace("http://w3id.org/CEON/ontology/processODP/")
CEONPR  = Namespace("http://w3id.org/CEON/ontology/process/")
CEONP   = Namespace("http://w3id.org/CEON/ontology/product/")

KG     = UNIT["KiloGM"]              # absolute mass (itemMass only, not statements)
FRAC   = UNIT["KiloGM-PER-KiloGM"]   # kg/kg mass fraction
GPERKG = UNIT["GM-PER-KiloGM"]       # g/kg mass fraction (readable small concentrations)

g = Graph()
for pfx, ns in [("futuram",FUT),("ex",EX),("dqv",DQV),("qudt",QUDT),
                ("unit",UNIT),("time",TIME),("ceonq",CEONQ),
                ("prov",PROV),("dcterms",DCTERMS),
                ("ceonpo",CEONPO),("ceonpr",CEONPR),("ceonp",CEONP),
                ("rdfs",RDFS),("xsd",XSD)]:
    g.bind(pfx, ns)

# ---- shared provenance individuals --------------------------------------
# Agents who asserted statements, and the sources they came from.
def agent(iri, name, kind):
    g.add((iri, RDF.type, PROV.Agent))
    g.add((iri, RDF.type, kind))   # prov:Person / prov:Organization
    g.add((iri, FUT.presentedBy if False else RDFS.label, Literal(name, lang="en")))

agent(EX.agent_futuramWP3, "FutuRaM WP3 dismantling team", PROV.Organization)
agent(EX.agent_oemDatasheet, "OEM material-declaration team", PROV.Organization)
agent(EX.agent_analyst, "Composition analyst (ORCID 0000-0001-8569-6630)", PROV.Person)

def source(iri, title, citation, url):
    g.add((iri, RDF.type, FUT.Source))
    g.add((iri, DCTERMS.title, Literal(title, lang="en")))
    g.add((iri, DCTERMS.bibliographicCitation, Literal(citation, lang="en")))
    g.add((iri, DCTERMS.source, Literal(url, datatype=XSD.anyURI)))

source(EX.src_oemMDS, "OEM Material Data Sheet (IMDS export)",
       "OEM, 2023. International Material Data System export for V0301030105.",
       "https://example.org/imds/V0301030105")
source(EX.src_dismantling, "FutuRaM ELV dismantling campaign 2022",
       "FutuRaM WP3, 2022. Dismantling and sampling campaign report.",
       "https://example.org/futuram/wp3/dismantling-2022")
source(EX.src_literature, "Literature review of BEV material composition",
       "Analyst, 2023. Review of published BEV composition data.",
       "https://example.org/futuram/litreview-bev")

# shared validity period (W3C Time) ---------------------------------------
g.add((EX.period_2020_2026, RDF.type, TIME.Interval))
g.add((EX.period_2020_2026, TIME.hasBeginning, EX.inst_2020))
g.add((EX.period_2020_2026, TIME.hasEnd, EX.inst_2026))
g.add((EX.inst_2020, RDF.type, TIME.Instant))
g.add((EX.inst_2020, TIME.inXSDDate, Literal("2020-01-01", datatype=XSD.date)))
g.add((EX.inst_2026, RDF.type, TIME.Instant))
g.add((EX.inst_2026, TIME.inXSDDate, Literal("2026-12-31", datatype=XSD.date)))

_stmt = 0
def interval(node, lo, hi, unit):
    # the interval is a distribution descriptor: best value (centre) +
    # optional min/max (spread) + required distribution shape (with params).
    best = (float(lo) + float(hi)) / 2.0
    for prop, v in [(FUT.hasBestValue, best),
                    (CEONQ.hasMinimalValueIncludedOfInterval, lo),
                    (CEONQ.hasMaximalValueIncludedOfInterval, hi)]:
        qv = BNode()
        g.add((qv, RDF.type, QUDT.QuantityValue))
        g.add((qv, QUDT.numericValue, Literal(float(v), datatype=XSD.double)))
        g.add((qv, QUDT.unit, unit))
        g.add((node, prop, qv))
    dist = BNode()
    g.add((dist, RDF.type, FUT.TriangularDistribution))
    g.add((dist, FUT.lowerBound, Literal(float(lo), datatype=XSD.double)))
    g.add((dist, FUT.upperBound, Literal(float(hi), datatype=XSD.double)))
    g.add((node, FUT.hasDistribution, dist))
    g.add((node, RDF.type, CEONQ.QuantityInterval))

def instance(iri, cls, label):
    g.add((iri, RDF.type, cls))
    g.add((iri, RDFS.label, Literal(label, lang="en")))

def process(iri, cls, label, see=None):
    """A manufacturing / recycling technology individual (a CEON Process)."""
    if (iri, RDF.type, cls) not in g:
        g.add((iri, RDF.type, cls))
        g.add((iri, RDF.type, CEONPO.Process))
        g.add((iri, RDFS.label, Literal(label, lang="en")))
        if see is not None:
            g.add((iri, RDFS.seeAlso, see))
    return iri

# GROUPED SHAPE: one futuram:CompositionStatement per WHOLE, carrying N qualified
# futuram:PartRelation nodes; the CS is minted once and cached, each statement()
# call adds one PartRelation.
_comp_of = {}                    # whole IRI -> its CompositionStatement IRI

def _composition_for(whole, src, who):
    cs = _comp_of.get(whole)
    if cs is None:
        cs = EX[f"comp_{whole.split('#')[-1]}"]
        _comp_of[whole] = cs
        g.add((cs, RDF.type, FUT.CompositionStatement))
        g.add((whole, FUT.hasCompositionStatement, cs))
        g.add((cs, CEONP.compositionOf, whole))
        g.add((cs, FUT.hasValidityPeriod, EX.period_2020_2026))
        g.add((cs, PROV.hadPrimarySource, src))
        g.add((cs, PROV.wasAttributedTo, who))
    return cs

def statement(whole, part, lo, hi, unit,
              src, who, production, recycling=None, completeness=None):
    """Add one qualified PartRelation (whole composed of lo..hi unit of part) to
    the whole's CompositionStatement. Identity is the plugin's job, so the relation
    node is a named-but-unhashed example IRI.
    """
    global _stmt
    _stmt += 1
    cs = _composition_for(whole, src, who)
    rel = EX[f"r{_stmt:03d}"]    # the qualified relation (addressable example IRI)
    q = BNode()                  # the quantity interval is an anonymous value
                                 # structure (blank node), like its endpoints
    g.add((rel, RDF.type, FUT.PartRelation))
    g.add((cs, FUT.hasPartRelation, rel))
    g.add((rel, FUT.refersTo, part))
    g.add((rel, FUT.hasQuantity, q))
    interval(q, lo, hi, unit)
    # manufacturing / recycling technology
    if production is not None:
        g.add((rel, FUT.hasProduction, production))
    if recycling is not None:
        g.add((rel, FUT.hasRecycling, recycling))
    if completeness is not None:
        m = BNode()              # DQV measurement is also an anonymous value
        g.add((m, RDF.type, DQV.QualityMeasurement))
        g.add((m, DQV.isMeasurementOf, FUT.CompletenessScore))
        g.add((m, DQV.value, Literal(float(completeness), datatype=XSD.double)))
        g.add((rel, DQV.hasQualityMeasurement, m))
    return rel

# ---- component-of-CAR statements (fraction of 1 kg of car) --------------
# Each statement is per 1 kg of the whole; components are a small fraction of the
# ~1450 kg car, so given in g/kg except the heavy battery (kg/kg).
# Tuple: (suffix, class, lo, hi, unit)
COMPONENTS = [
    ("harness",   FUT.wiringHarness,                 1.9, 2.2,  GPERKG),  # ~2 g of harness per kg car
    ("motor",     FUT.elvElectricMotor,              5.2, 5.9,  GPERKG),  # ~5.5 g/kg
    ("cables",    FUT.elvEmbeddedElectronicsCables,  1.0, 1.2,  GPERKG),  # ~1.1 g/kg
    ("battery",   FUT.elvEVbattery,                  0.21, 0.25, FRAC),   # ~0.23 kg/kg (battery is heavy)
]

# ---- materials within each component (fraction of 1 kg of the component) -
# Each material is a fraction of its component and decomposes into elements;
# only some materials are copper-bearing.
# Tuple: (suffix, class, lo, hi, unit, [ (element_suffix, ElementClass, frac_lo, frac_hi), ... ])
MATERIALS = {
    # wiring harness: copper conductor + a plastics insulation sheath
    "harness": [
        ("cu",      FUT.pureCu,           0.58, 0.62, FRAC, [("cu", FUT.Copper, 0.99, 1.0)]),
        ("plastic", FUT.thermoplastics,   0.36, 0.40, FRAC, [("c",  FUT.Carbon, 0.60, 0.75)]),
    ],
    # electric motor: copper winding + NdFeB magnet (Nd+Fe) + steel rotor (Fe)
    # (~8 kg motor: winding ~0.15, magnet ~0.11, steel ~0.61 kg/kg)
    "motor": [
        ("cu",      FUT.pureCu,                0.14, 0.16, FRAC, [("cu", FUT.Copper, 0.99, 1.0)]),
        ("magnet",  FUT.magnetAlloysNdFeB,     0.10, 0.12, FRAC, [("nd", FUT.Neodymium, 0.28, 0.32),
                                                                  ("fe", FUT.Iron,      0.62, 0.68)]),
        ("steel",   FUT.steelAndSteelAlloys,   0.58, 0.64, FRAC, [("fe", FUT.Iron, 0.97, 0.99)]),
    ],
    # embedded-electronics cables: copper conductor + plastics
    "cables": [
        ("cu",      FUT.pureCu,           0.60, 0.66, FRAC, [("cu", FUT.Copper, 0.99, 1.0)]),
        ("plastic", FUT.thermoplastics,   0.30, 0.36, FRAC, [("c",  FUT.Carbon, 0.60, 0.75)]),
    ],
    # EV battery: copper busbars + aluminium casing (NO copper in the alu path)
    "battery": [
        ("cu",      FUT.CuAndCuAlloys,    0.07, 0.09, FRAC, [("cu", FUT.Copper, 0.95, 0.99)]),
        ("alu",     FUT.AlAndAlAlloys,    0.18, 0.22, FRAC, [("al", FUT.Aluminium, 0.97, 0.99)]),
    ],
}

# ---- manufacturing-technology individuals (CEON Processes) --------------
# One production process per kind of edge; element-fraction edges use an
# 'unknown' process (demonstrating the allowed unknown-but-present value).
PROC_ASSEMBLY = process(EX.proc_assembly, CEONPR.AssemblingProcess,
                        "vehicle assembly", CEONPR.ManufacturingProcess)
PROC_FORM     = process(EX.proc_forming, CEONPR.ManufacturingProcess,
                        "material forming / shaping")
PROC_UNKNOWN  = process(EX.proc_unknown, CEONPO.Process,
                        "unknown production process")
# recycling technology (optional, shown on copper-bearing material edges)
RECYC_CU      = process(EX.proc_recycling_cu, CEONPR.RecycleProcess,
                        "copper recovery / recycling")

CARS = [
    ("carA", "BEV car instance A", set()),               # complete
    ("carB", "BEV car instance B", {("motor","cu")}),    # drop motor-cu chain
]

# source + agent per edge level: component data from the OEM datasheet,
# material data from the dismantling campaign, element fractions from the
# literature review.
SRC_BY_LEVEL = {
    0: (EX.src_oemMDS,      EX.agent_oemDatasheet),
    1: (EX.src_dismantling, EX.agent_futuramWP3),
    2: (EX.src_literature,  EX.agent_analyst),
}

for car_id, car_label, skip in CARS:
    car = EX[car_id]
    instance(car, FUT.V0301030105, car_label)
    for csuf, ccls, clo, chi, cunit in COMPONENTS:
        comp = EX[f"{car_id}_{csuf}"]
        instance(comp, ccls, f"{car_label} {csuf}")
        src, who = SRC_BY_LEVEL[0]
        statement(car, comp, clo, chi, cunit,              # component fraction of car
                  src, who, production=PROC_ASSEMBLY,
                  completeness=3.0 if csuf == "harness" else None)
        for msuf, mcls, mlo, mhi, munit, elements in MATERIALS[csuf]:
            if (csuf, msuf) in skip:
                # incomplete path: declare the material node but state nothing
                mat = EX[f"{car_id}_{csuf}_{msuf}"]
                instance(mat, mcls, f"{car_label} {csuf} {msuf} (incomplete)")
                continue
            mat = EX[f"{car_id}_{csuf}_{msuf}"]
            instance(mat, mcls, f"{car_label} {csuf} {msuf}")
            src, who = SRC_BY_LEVEL[1]
            is_cu_mat = any(e[1] == FUT.Copper for e in elements)
            statement(comp, mat, mlo, mhi, munit,           # material: formed
                      src, who, production=PROC_FORM,
                      recycling=RECYC_CU if is_cu_mat else None)
            # each material decomposes into its constituent elements (fractions)
            for esuf, ecls, elo, ehi in elements:
                el = EX[f"{car_id}_{csuf}_{msuf}_{esuf}"]
                instance(el, ecls, f"{car_label} {csuf} {msuf} {esuf} (element)")
                src, who = SRC_BY_LEVEL[2]
                statement(mat, el, elo, ehi, FRAC,           # element: unknown proc
                          src, who, production=PROC_UNKNOWN)

header = """#################################################################
#
#    Worked example: "how much copper is in a (BEV) car?"  (REALISTIC)
#
#    Generated by gen_sample.py (uv run + rdflib). Each car is a
#    V0301030105 (Executive/Large BEV) and fans out into FOUR components
#    (wiring harness, electric motor, embedded-electronics cables, EV
#    battery), each of which fans out into several materials. Copper
#    therefore reaches the car on MANY paths (harness wire, motor winding,
#    cable wire, battery busbars), so the per-car copper total is a sum
#    over several chains and the class total is the equal mean of the two
#    cars.
#
#    Only classes that exist in futuram-hierarchy.ttl are used. Units are
#    mixed (kg component/material masses vs kg-per-kg mass fractions) so
#    the aggregator must resolve each edge to an absolute mass. Every edge
#    carries a min/max ceon:QuantityInterval. Car B's motor-copper path is
#    intentionally INCOMPLETE (material node present, no statement) to
#    exercise report+exclude coverage.
#
#    GROUPED SHAPE: each WHOLE has one futuram:CompositionStatement (a
#    ceon:Composition) carrying N qualified futuram:PartRelation nodes
#    (one per part, linked by futuram:refersTo). No futuram:hasPart triples
#    are asserted: the part-of edges are entailed by the property-chain
#    axiom on futuram:hasPart (hasCompositionStatement o hasPartRelation o
#    refersTo) and then transitively closed. Validate together with
#    futuram-hierarchy.ttl, composition-statement.ttl and
#    composition-statement-shapes.ttl.
#
#################################################################

"""
body = g.serialize(format="turtle")
import pathlib
_OUT = pathlib.Path(__file__).resolve().parent.parent / "ontology" / "abox" / "example" / "sample-instances.ttl"
with open(_OUT, "w") as f:
    f.write(header + body)
print(f"wrote {len(g)} triples, {_stmt} composition statements")

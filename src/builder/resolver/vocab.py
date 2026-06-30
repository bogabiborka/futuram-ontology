"""resolver.vocab — namespaces, level/unknown/unit constants, and the pure stateless
helpers the projection layer builds on. Everything is DERIVED from the model, never a
hardcoded bare string: the four levels are real OWL classes, mapped name<->IRI here.
"""
from rdflib import Namespace, RDFS

from common.vocab import LEVELS

FQ = Namespace("https://www.purl.org/futuram/query#")
FUT = Namespace("https://www.purl.org/futuram#")

# level string (common.vocab.LEVELS) <-> its OWL class IRI
LEVEL_CLASS = {level: FUT[level] for level in LEVELS}
CLASS_LEVEL = {iri: level for level, iri in LEVEL_CLASS.items()}

# the four composition levels, as named strings (never bare literals)
PRODUCT, COMPONENT, MATERIAL, ELEMENT = LEVELS

# the TBox's first-class remainder placeholders (one per level)
UNKNOWN_PRODUCT = "unknownProduct"
UNKNOWN_COMPONENT = "unknownComponent"
UNKNOWN_MATERIAL = "unknownMaterial"
UNKNOWN_ELEMENT = "unknownElement"
UNKNOWN_FOR_LEVEL = {
    PRODUCT: UNKNOWN_PRODUCT,
    COMPONENT: UNKNOWN_COMPONENT,
    MATERIAL: UNKNOWN_MATERIAL,
    ELEMENT: UNKNOWN_ELEMENT,
}

# the per-kg-of-whole unit carried on every served amount
UNIT = "kg/kg"

# field names of the frozen oracle's return dicts (aggregate_mc -> {best,lo,hi};
# coarse_fine -> {...,unknown_min}) — centralised so no bare key string scatters.
K_LO, K_HI = "lo", "hi"
K_UNKNOWN_MIN = "unknown_min"


def class_iri(class_name):
    """Oracle class NAME -> its futuram IRI (the served graph keys classes by IRI)."""
    return FUT[class_name]


def element_iri(element_class):
    """Constituent class name -> futuram IRI (e.g. 'Copper' -> futuram:Copper)."""
    return FUT[element_class]


def amount_iri(subject_iri, constituent_class):
    """Stable IRI for the fq:Amount node of one (whole class, constituent) pair. The
    ontology guarantees ONE fq:Amount per pair, so this content-addressed key makes the
    served graph addressable and union-idempotent (re-emitting the same pair is a no-op)."""
    s = str(subject_iri).split("#")[-1]
    return FQ[f"amount_{s}_{constituent_class}"]


def local(iri):
    """Local name of a futuram: IRI (the bare class name the emitter takes)."""
    return str(iri).split("#")[-1]


def next_level(level):
    """The level one step DOWN (Product->Component->Material->Element), or None
    at Element. Read from common.vocab.LEVELS — never hardcoded."""
    levels = list(LEVELS)
    i = levels.index(level)
    return levels[i + 1] if i + 1 < len(levels) else None


def scope_span(entry):
    """(first_year, last_year) of a class_time entry."""
    if "year" in entry:
        return entry["year"], entry["year"]
    return entry["start"], entry["end"]


def subclass_of(g, sub, sup):
    """sub rdfs:subClassOf* sup over a TBox graph (transitive, reflexive)."""
    if sub == sup:
        return True
    seen, stack = set(), [sub]
    while stack:
        cur = stack.pop()
        for parent in g.objects(cur, RDFS.subClassOf):
            if parent == sup:
                return True
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return False

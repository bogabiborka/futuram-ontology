"""builder.relation_identity — the IDENTITY pass that content-addresses every part-
relation: skolemize BLANK relations to futuram:stmt_<sha1(source|whole|part|best|lo|hi|
unit)>, leave author IRIs intact, stamp futuram:contentHash. Run BEFORE store dedup.
"""
from __future__ import annotations

import hashlib

from rdflib import Graph, BNode, URIRef, Literal, RDF
from rdflib.namespace import XSD

from common.vocab import FUT, UNIT_BY_NAME


def _hash(source, whole, part, best, lo, hi, unit):
    """sha1(source|whole|part|best|lo|hi|unit) — byte-identical to common.vocab.stmt_iri,
    re-keyed on the RELATION (the 1:1-with-edge node)."""
    key = "|".join((str(source), str(whole), str(part),
                    repr(float(best)), repr(float(lo)), repr(float(hi)), str(unit)))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _rel_facts(g, rel):
    """Read (whole, part, best, lo, hi, unit-name) for a relation, for hashing. whole
    = the hasCompositionStatement subject of the statement that hasPartRelation -> rel;
    part = refersTo; best/lo/hi/unit from the quantity. None if incomplete (skip)."""
    part = g.value(rel, FUT.refersTo)
    if part is None:
        return None
    stmt = next(iter(g.subjects(FUT.hasPartRelation, rel)), None)
    whole = g.value(predicate=FUT.hasCompositionStatement, object=stmt) if stmt else None
    q = g.value(rel, FUT.hasQuantity)
    if q is None or whole is None:
        return None
    from common.vocab import QUDT, CEONQ
    bestv = g.value(g.value(q, FUT.hasBestValue), QUDT.numericValue)
    unit = g.value(g.value(q, FUT.hasBestValue), QUDT.unit)
    lo_node = g.value(q, CEONQ.hasMinimalValueIncludedOfInterval)
    hi_node = g.value(q, CEONQ.hasMaximalValueIncludedOfInterval)
    lo = g.value(lo_node, QUDT.numericValue) if lo_node is not None else bestv
    hi = g.value(hi_node, QUDT.numericValue) if hi_node is not None else bestv
    if bestv is None or unit is None:
        return None
    # unit IRI -> short name (gkg/kgkg) so the hash matches stmt_iri's unit token
    unit_name = next((n for n, u in UNIT_BY_NAME.items() if u == unit), str(unit))
    return (str(whole).rsplit("#", 1)[-1].rsplit("/", 1)[-1],
            str(part).rsplit("#", 1)[-1].rsplit("/", 1)[-1],
            float(bestv), float(lo), float(hi), unit_name)


def stamp_identity(g: Graph, *, source: str = "") -> Graph:
    """Stamp content-addressed identity onto every futuram:PartRelation in `g` (mutates
    and returns it): skolemize blanks to futuram:stmt_<hash>, leave author IRIs intact,
    add futuram:contentHash. Idempotent; `source` qualifies the hash per source."""
    rels = list(g.subjects(RDF.type, FUT.PartRelation))
    for rel in rels:
        facts = _rel_facts(g, rel)
        if facts is None:
            continue
        whole, part, best, lo, hi, unit_name = facts
        h = _hash(source, whole, part, best, lo, hi, unit_name)
        target = rel
        if isinstance(rel, BNode):
            target = FUT["stmt_" + h]
            # move every triple on/to the blank node onto the skolem IRI
            for p, o in list(g.predicate_objects(rel)):
                g.remove((rel, p, o)); g.add((target, p, o))
            for s, p in list(g.subject_predicates(rel)):
                g.remove((s, p, rel)); g.add((s, p, target))
        if (target, FUT.contentHash, None) not in g:
            g.add((target, FUT.contentHash, Literal(h, datatype=XSD.string)))
    return g

"""resolver.emit_helpers — ONLY the shared fq:/QUDT write-SHAPES.

Write-shapes emitted IDENTICALLY by several plugins (the fq:Amount stable-IRI
pattern, typing a class, the scope literals). No single plugin's logic lives here.
"""
from __future__ import annotations

from rdflib import Literal, RDF, RDFS, OWL, XSD

from .vocab import (FQ, LEVEL_CLASS, UNIT, ELEMENT, class_iri, element_iri,
                    amount_iri, scope_span)


def class_node(g, class_name):
    """Type `class_name` as owl:Class; return its IRI. Idempotent (several plugins
    may type the same class — RDF merges the duplicate triple)."""
    cls = class_iri(class_name)
    g.add((cls, RDF.type, OWL.Class))
    return cls


def amount(g, subject, whole_iri, constituent_class, value, *,
           level_class=None, lo=None, hi=None, with_dist=False, rel_u=None,
           mean_dq=None, dqs=None):
    """Emit one fq:Amount hung off `subject` via fq:contains, returning the amount node.
    The constituent's KIND is its subClassOf edge to `level_class` (default Element).
    Optional `rel_u`/`mean_dq`/`dqs` stamp the aggregated uncertainty + data quality."""
    if level_class is None:
        level_class = LEVEL_CLASS[ELEMENT]
    # stable, content-addressed IRI (one fq:Amount per (subject, constituent)) —
    # addressable + union-idempotent, replacing the old anonymous blank node.
    a = amount_iri(subject, constituent_class)
    c = element_iri(constituent_class)
    g.add((a, RDF.type, FQ.Amount))
    g.add((a, FQ.constituent, c))
    g.add((a, FQ.whole, whole_iri))
    g.add((c, RDFS.subClassOf, level_class))
    # round to 9 dp (golden precision): a stable-IRI Amount must be IDEMPOTENT under
    # re-emission, so two plugins computing the same value up to float epsilon emit the
    # IDENTICAL triple and RDF merges them into one node.
    g.add((a, FQ.amount, Literal(round(float(value), 9), datatype=XSD.double)))
    if lo is not None:
        g.add((a, FQ.amountLow, Literal(round(float(lo), 9), datatype=XSD.double)))
    if hi is not None:
        g.add((a, FQ.amountHigh, Literal(round(float(hi), 9), datatype=XSD.double)))
    g.add((a, FQ.unit, Literal(UNIT)))
    if rel_u is not None:
        g.add((a, FQ.relativeUncertainty,
               Literal(round(float(rel_u), 9), datatype=XSD.double)))
    if mean_dq is not None:
        g.add((a, FQ.meanDataQuality,
               Literal(round(float(mean_dq), 9), datatype=XSD.double)))
    if dqs is not None:
        g.add((a, FQ.dqs, Literal(int(dqs), datatype=XSD.integer)))
    if with_dist:
        dist = FQ[f"dist_{str(a).split('#')[-1]}"]   # stable, tied to the amount
        g.add((dist, RDF.type, FQ.Empirical))
        g.add((a, FQ.distribution, dist))
    g.add((subject, FQ.contains, a))
    return a


def scope(g, iri, entry):
    """The bare year/period literals of one class_time entry onto `iri`."""
    y0, y1 = scope_span(entry)
    if y0 == y1:
        g.add((iri, FQ.referenceYear, Literal(int(y0), datatype=XSD.integer)))
    else:
        g.add((iri, FQ.periodStart, Literal(int(y0), datatype=XSD.integer)))
        g.add((iri, FQ.periodEnd, Literal(int(y1), datatype=XSD.integer)))

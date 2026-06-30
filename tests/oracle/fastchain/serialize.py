# (fastchain package — split from the single-file implementation; the
#  arithmetic is verbatim, only the module layout changed.)
"""Serialisation: to_graph() / write() — the RDF emitter.
Mixed into SupplyChain (chain.py).
"""
import hashlib

from rdflib import Graph, BNode, Literal, RDF, RDFS
from rdflib.namespace import XSD

from .vocab import (FUT, EX, PROV, QUDT, UNIT, TIME, CEONQ, CEONPR, CEONPO,
                    DQV)


def _stmt_iri(s, source):
    """Stable content-addressed IRI futuram:stmt_<sha1(source|whole|part|best|lo|hi|unit)>: identical content -> identical IRI (re-union idempotent; a conflicting re-statement of a (whole, part) gets a SECOND IRI). `source` separates distinct sources; whole/part names encode the time/drivetrain slice; values repr()'d for exact round-trip.
    """
    key = "|".join((
        str(source),
        str(s.whole), str(s.part),
        repr(float(s.best)), repr(float(s.lo)), repr(float(s.hi)),
        str(s.unit),
    ))
    return FUT["stmt_" + hashlib.sha1(key.encode("utf-8")).hexdigest()]


class SerializationMixin:
    # ---- serialisation --------------------------------------------------
    def to_graph(self, full_metadata=True):
        """Emit the Turtle graph. If full_metadata, attach the required
        validity/provenance/production so the data is SHACL-valid; tests probing
        missing metadata pass full_metadata=False."""
        g = Graph()
        for pfx, ns in [("futuram", FUT), ("ex", EX), ("prov", PROV),
                        ("qudt", QUDT), ("unit", UNIT), ("time", TIME),
                        ("ceonq", CEONQ), ("ceonpr", CEONPR), ("ceonpo", CEONPO),
                        ("dqv", DQV), ("rdfs", RDFS), ("xsd", XSD)]:
            g.bind(pfx, ns)

        prov = self.provenance
        # shared provenance/validity/production individuals, from the scenario's
        # provenance block (no hardcoded defaults).
        period = EX["period"]
        g.add((period, RDF.type, TIME.Interval))
        b = BNode(); g.add((b, RDF.type, TIME.Instant)); g.add((b, TIME.inXSDDate, Literal(prov["validFrom"], datatype=XSD.date))); g.add((period, TIME.hasBeginning, b))
        if prov.get("validUntil"):
            e = BNode(); g.add((e, RDF.type, TIME.Instant)); g.add((e, TIME.inXSDDate, Literal(prov["validUntil"], datatype=XSD.date))); g.add((period, TIME.hasEnd, e))
        proc = EX["proc"]; g.add((proc, RDF.type, CEONPR.ManufacturingProcess)); g.add((proc, RDF.type, CEONPO.Process)); g.add((proc, RDFS.label, Literal(prov["production"], lang="en")))
        src = EX["src"]; g.add((src, RDF.type, FUT.Source)); g.add((src, RDFS.label, Literal(prov["source"], lang="en")))
        agent = EX["agent"]; g.add((agent, RDF.type, PROV.Agent)); g.add((agent, RDF.type, PROV.Organization)); g.add((agent, RDFS.label, Literal(prov["agent"], lang="en")))

        # subclass edges the local hierarchy lacks, so RDFS typing matches the
        # oracle (such an instance resolves to futuram:Product). Values may be a
        # name or a list (a time slice has its base AND the ancestor slice).
        for sub, sups in self.subclass_of.items():
            for sup in ([sups] if isinstance(sups, str) else sups):
                g.add((FUT[sub], RDFS.subClassOf, FUT[sup]))

        # the time registry as class annotations: futuram:referenceYear /
        # hasReferencePeriod, plus GENERIC slice edges (futuram:sliceOf parent ;
        # sliceAxis <strategy>) + declared strategy. from_graph round-trips these.
        from .vocab import STRATEGY_IRI
        for cls, entry in sorted(self.class_time.items()):
            ciri = FUT[cls]
            if "year" in entry:
                g.add((ciri, FUT.referenceYear,
                       Literal(int(entry["year"]), datatype=XSD.int)))
            else:
                per = EX[f"refperiod_{cls}"]
                g.add((per, RDF.type, TIME.Interval))
                pb = BNode(); g.add((pb, RDF.type, TIME.Instant))
                g.add((pb, TIME.inXSDDate,
                       Literal(f"{int(entry['start'])}-01-01",
                               datatype=XSD.date)))
                g.add((per, TIME.hasBeginning, pb))
                pe = BNode(); g.add((pe, RDF.type, TIME.Instant))
                g.add((pe, TIME.inXSDDate,
                       Literal(f"{int(entry['end'])}-12-31",
                               datatype=XSD.date)))
                g.add((per, TIME.hasEnd, pe))
                g.add((ciri, FUT.hasReferencePeriod, per))
            for parent, axis in entry.get("slices", ()):
                g.add((ciri, FUT.sliceOf, FUT[parent]))
                g.add((ciri, FUT.sliceAxis, FUT[STRATEGY_IRI[axis]]))
            if entry.get("strategy"):
                g.add((ciri, FUT.hasAggregationStrategy,
                       FUT[STRATEGY_IRI[entry["strategy"]]]))

        for n in self.nodes.values():
            g.add((n.iri, RDF.type, FUT[n.cls]))
            g.add((n.iri, RDFS.label, Literal(n.name, lang="en")))
            # itemMass (absolute kg) — the reference anchor on Product/Component
            # instances, as a QUDT quantity value in kilograms.
            if n.item_mass is not None:
                qv = BNode(); g.add((qv, RDF.type, QUDT.QuantityValue))
                g.add((qv, QUDT.numericValue,
                       Literal(float(n.item_mass), datatype=XSD.double)))
                g.add((qv, QUDT.unit, UNIT.KiloGM))
                g.add((n.iri, FUT.itemMass, qv))

        # `source` for the statement hash: the chain's provenance source plus its
        # id, so two genuinely different sources never collide on one statement
        # IRI even when whole/part/value coincide.
        stmt_source = f"{prov.get('source', '')}|{self.id or ''}"
        # GROUPED SHAPE (mirrors etl.composition_rdf): one CompositionStatement per
        # WHOLE (a ceon:Composition) carrying N PartRelations. Identity (contentHash,
        # skolemization) is the identity PASS's job, NOT this serializer's.
        from common.vocab import CEONP
        by_whole = {}
        for s in self.stmts:
            by_whole.setdefault(s.whole, []).append(s)
        from rdflib import URIRef
        for whole, stmts in by_whole.items():
            cs = URIRef(self.nodes[whole].iri + "__comp")
            g.add((cs, RDF.type, FUT.CompositionStatement))
            g.add((self.nodes[whole].iri, FUT.hasCompositionStatement, cs))
            g.add((cs, CEONP.compositionOf, self.nodes[whole].iri))
            if full_metadata:
                g.add((cs, FUT.hasValidityPeriod, period))
                g.add((cs, PROV.hadPrimarySource, src))
                g.add((cs, PROV.wasAttributedTo, agent))
            for s in stmts:
                si = _stmt_iri(s, stmt_source)
                g.add((si, RDF.type, FUT.PartRelation))
                g.add((cs, FUT.hasPartRelation, si))
                g.add((si, FUT.refersTo, self.nodes[s.part].iri))
                q = BNode(); g.add((q, RDF.type, CEONQ.QuantityInterval))
                emit = [(FUT.hasBestValue, s.best)]
                if s.has_hard_lower_bound:
                    emit += [(CEONQ.hasMinimalValueIncludedOfInterval, s.lo),
                             (CEONQ.hasMaximalValueIncludedOfInterval, s.hi)]
                for prop, v in emit:
                    qv = BNode(); g.add((qv, RDF.type, QUDT.QuantityValue))
                    g.add((qv, QUDT.numericValue, Literal(float(v), datatype=XSD.double)))
                    g.add((qv, QUDT.unit, s.unit)); g.add((q, prop, qv))
                dcls, dparams = s.distribution()
                dist = BNode(); g.add((dist, RDF.type, FUT[dcls]))
                for pname, pval in dparams.items():
                    g.add((dist, FUT[pname], Literal(pval, datatype=XSD.double)))
                g.add((q, FUT.hasDistribution, dist))
                g.add((si, FUT.hasQuantity, q))
                if full_metadata:
                    g.add((si, FUT.hasProduction, proc))
                    for dim, score in s.quality.items():
                        qm = BNode()
                        g.add((qm, RDF.type, DQV.QualityMeasurement))
                        g.add((qm, DQV.value, Literal(float(score), datatype=XSD.double)))
                        g.add((qm, DQV.isMeasurementOf, FUT[f"{dim}Score"]))
                        g.add((si, DQV.hasQualityMeasurement, qm))
        return g

    def write(self, path, full_metadata=True, header=""):
        g = self.to_graph(full_metadata=full_metadata)
        body = g.serialize(format="turtle")
        with open(path, "w") as f:
            if header:
                # ensure EVERY header line is a Turtle comment (notes may be
                # multi-line; an un-commented continuation line breaks parsing)
                commented = "\n".join(
                    ln if ln.lstrip().startswith("#") else f"# {ln}"
                    for ln in header.rstrip().split("\n"))
                f.write(commented + "\n\n")
            f.write(body)
        return g

# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///
"""etl.composition_rdf — emit composition-statement RDF DIRECTLY from a transform
doc (CSV->dict) with rdflib, importing ONLY common (no oracle). Byte-equivalent to
the frozen oracle serializer PLUS the generic axis-VALUE marker (futuram:sliceAxis).
"""
from __future__ import annotations

from types import SimpleNamespace

from rdflib import Graph, BNode, Literal, RDF, RDFS
from rdflib.namespace import XSD

from common.vocab import (FUT, EX, PROV, QUDT, UNIT, TIME, CEONP, CEONQ, CEONPR,
                          CEONPO, DQV, UNIT_BY_NAME, STRATEGY_IRI,
                          stmt_iri as _stmt_iri)

# distribution kind -> (futuram class localname, required param names). Same table
# as the oracle model's DIST_KINDS; triangular/uniform default lower/upperBound to
# the statement's lo/hi.
_DIST_KINDS = {
    "triangular": ("TriangularDistribution", ("lowerBound", "upperBound")),
    "uniform":    ("UniformDistribution",    ("lowerBound", "upperBound")),
    "rectangular": ("RectangularDistribution", ()),
    "normal":     ("NormalDistribution",     ("stdDev",)),
    "lognormal":  ("LogNormalDistribution",  ("logStdDev",)),
    "beta":       ("BetaDistribution",       ("alpha", "beta")),
    "gamma":      ("GammaDistribution",      ("shapeParam", "scaleParam")),
    "weibull":    ("WeibullDistribution",    ("shapeParam", "scaleParam")),
}
# kinds that emit an interval [min,max] on the QuantityInterval. RECTANGULAR does
# NOT: it stores only its half-width on the distribution node; the endpoints are
# DERIVED downstream as best x (1 -/+ limit), never stored.
_HARD_LOWER = {"triangular", "uniform"}

# Remainder/catch-all classes the ETL must NEVER author: OMITTED entirely (node +
# referencing statements) so named constituents sum < 1.0 and balance() RE-INFERS
# the residual. Match is PRECISE (prefix otherOrUndefined…/unknown… or exact elvRest).
_PLACEHOLDER_CLASSES = {"elvRest"}


def _is_placeholder_class(cls_localname: str) -> bool:
    """True iff a class localname is a remainder/catch-all that the ETL must omit
    so the resolver re-infers the residual instead of the ETL authoring it."""
    s = str(cls_localname)
    return (s.startswith("otherOrUndefined") or s.startswith("unknown")
            or s in _PLACEHOLDER_CLASSES)

# statement keys handled explicitly; every OTHER key is a distribution parameter
# (scenario YAMLs carry dist params INLINE, e.g. stdDev: 0.01).
_CORE = {"whole", "part", "best", "lo", "hi", "unit", "dist",
         "quality", "dist_params"}


def _stmt_view(s):
    """A statement doc-dict as the attribute view stmt_iri expects (.whole/.part/
    .best/.lo/.hi/.unit) — stmt_iri is content-addressed on these, byte-identical
    to the oracle's."""
    return SimpleNamespace(
        whole=s["whole"], part=s["part"],
        best=float(s["best"]),
        lo=float(s.get("lo", s["best"])), hi=float(s.get("hi", s["best"])),
        unit=UNIT_BY_NAME[s["unit"]])


def _distribution(s):
    """(futuram dist-class localname, {param: value}) from a statement dict. Params
    are the INLINE non-core keys (scenario YAML shape) plus any nested dist_params
    (transform-doc shape)."""
    kind = s["dist"]
    cls, required = _DIST_KINDS[kind]
    params = {k: float(v) for k, v in s.items() if k not in _CORE}
    params.update({k: float(v) for k, v in (s.get("dist_params") or {}).items()})
    if kind in ("triangular", "uniform"):
        params.setdefault("lowerBound", float(s.get("lo", s["best"])))
        params.setdefault("upperBound", float(s.get("hi", s["best"])))
    missing = [p for p in required if p not in params]
    if missing:
        raise ValueError(f"distribution {kind!r} on {s['whole']}->{s['part']} "
                         f"missing required param(s) {missing}")
    return cls, {p: float(params[p]) for p in required}


def composition_rdf(doc, *, full_metadata=True, axis_values=None):
    """Emit composition RDF for a transform `doc` (pure rdflib, common only).
    `axis_values` is an optional {class_localname: axis_strategy_token} map stamping
    the generic axis-VALUE marker (futuram:sliceAxis) the ValueAxisSlicer reads."""
    g = Graph()
    for pfx, ns in [("futuram", FUT), ("ex", EX), ("prov", PROV), ("qudt", QUDT),
                    ("unit", UNIT), ("time", TIME), ("ceonp", CEONP), ("ceonq", CEONQ),
                    ("ceonpr", CEONPR), ("ceonpo", CEONPO), ("dqv", DQV),
                    ("rdfs", RDFS), ("xsd", XSD)]:
        g.bind(pfx, ns)

    prov = doc.get("provenance") or {}
    period = EX["period"]
    g.add((period, RDF.type, TIME.Interval))
    b = BNode(); g.add((b, RDF.type, TIME.Instant)); g.add((b, TIME.inXSDDate, Literal(prov["validFrom"], datatype=XSD.date))); g.add((period, TIME.hasBeginning, b))
    if prov.get("validUntil"):
        e = BNode(); g.add((e, RDF.type, TIME.Instant)); g.add((e, TIME.inXSDDate, Literal(prov["validUntil"], datatype=XSD.date))); g.add((period, TIME.hasEnd, e))
    proc = EX["proc"]; g.add((proc, RDF.type, CEONPR.ManufacturingProcess)); g.add((proc, RDF.type, CEONPO.Process)); g.add((proc, RDFS.label, Literal(prov["production"], lang="en")))
    src = EX["src"]; g.add((src, RDF.type, FUT.Source)); g.add((src, RDFS.label, Literal(prov["source"], lang="en")))
    agent = EX["agent"]; g.add((agent, RDF.type, PROV.Agent)); g.add((agent, RDF.type, PROV.Organization)); g.add((agent, RDFS.label, Literal(prov["agent"], lang="en")))

    # subclass edges (e.g. V0301030101 rdfs:subClassOf elvBEV). NB: time-slice
    # subClassOf edges are NOT authored here — the plugin layer derives them.
    for sub, sups in (doc.get("subclass_of") or {}).items():
        for sup in ([sups] if isinstance(sups, str) else sups):
            g.add((FUT[sub], RDFS.subClassOf, FUT[sup]))

    # rich rdfs:labels on the vehicle product CLASSES (drivetrain + segment + code),
    # so a segment-phrased question resolves to the right class by label lookup.
    for cls, label in (doc.get("class_labels") or {}).items():
        g.add((FUT[cls], RDFS.label, Literal(label, lang="en")))

    # INSTANCE time as DATA (node name -> {year} | {start,end}): emitted on the
    # instance INDIVIDUAL, never as a `_Y` class. builder.slicer.YearSlicer reads
    # this referenceYear/period and derives every time-slice class downstream.
    for name, scope in (doc.get("node_time") or {}).items():
        iri = EX[name]
        if "year" in scope:
            g.add((iri, FUT.referenceYear, Literal(int(scope["year"]), datatype=XSD.int)))
        else:
            per = EX[f"refperiod_{name}"]
            g.add((per, RDF.type, TIME.Interval))
            pb = BNode(); g.add((pb, RDF.type, TIME.Instant))
            g.add((pb, TIME.inXSDDate, Literal(f"{int(scope['start'])}-01-01", datatype=XSD.date)))
            g.add((per, TIME.hasBeginning, pb))
            pe = BNode(); g.add((pe, RDF.type, TIME.Instant))
            g.add((pe, TIME.inXSDDate, Literal(f"{int(scope['end'])}-12-31", datatype=XSD.date)))
            g.add((per, TIME.hasEnd, pe))
            g.add((iri, FUT.hasReferencePeriod, per))

    # the time registry as class annotations + the generic slice edges.
    for cls, entry in sorted((doc.get("class_time") or {}).items()):
        ciri = FUT[cls]
        if "year" in entry:
            g.add((ciri, FUT.referenceYear, Literal(int(entry["year"]), datatype=XSD.int)))
        else:
            per = EX[f"refperiod_{cls}"]
            g.add((per, RDF.type, TIME.Interval))
            pb = BNode(); g.add((pb, RDF.type, TIME.Instant))
            g.add((pb, TIME.inXSDDate, Literal(f"{int(entry['start'])}-01-01", datatype=XSD.date)))
            g.add((per, TIME.hasBeginning, pb))
            pe = BNode(); g.add((pe, RDF.type, TIME.Instant))
            g.add((pe, TIME.inXSDDate, Literal(f"{int(entry['end'])}-12-31", datatype=XSD.date)))
            g.add((per, TIME.hasEnd, pe))
            g.add((ciri, FUT.hasReferencePeriod, per))
        for item in entry.get("slices", ()):
            parent = item["parent"] if isinstance(item, dict) else item[0]
            axis = (item.get("axis", "year-slice-mean") if isinstance(item, dict)
                    else (item[1] if len(item) > 1 else "year-slice-mean"))
            g.add((ciri, FUT.sliceOf, FUT[parent]))
            g.add((ciri, FUT.sliceAxis, FUT[STRATEGY_IRI[axis]]))
        if entry.get("strategy"):
            g.add((ciri, FUT.hasAggregationStrategy, FUT[STRATEGY_IRI[entry["strategy"]]]))

    # the GENERIC axis-VALUE marker (this layer's addition): a value-class carries
    # futuram:sliceAxis <strategyIRI> (same predicate a slice carries) so the generic
    # ValueAxisSlicer finds the axis values from the graph, no source-layout knowledge.
    for cls, token in (axis_values or {}).items():
        g.add((FUT[cls], FUT.sliceAxis, FUT[STRATEGY_IRI[token]]))

    # Remainder placeholder nodes (otherOrUndefined* / elvRest) are OMITTED (node +
    # referencing statements), so the resolver re-infers the residual. Collected
    # first so a statement whose WHOLE or PART is one is skipped too.
    omit_nodes = {name for name, spec in doc["nodes"].items()
                  if _is_placeholder_class(spec["class"])}

    # nodes
    for name, spec in doc["nodes"].items():
        if name in omit_nodes:
            continue
        iri = EX[name]
        g.add((iri, RDF.type, FUT[spec["class"]]))
        g.add((iri, RDFS.label, Literal(name, lang="en")))
        if spec.get("itemMass") is not None:
            qv = BNode(); g.add((qv, RDF.type, QUDT.QuantityValue))
            g.add((qv, QUDT.numericValue, Literal(float(spec["itemMass"]), datatype=XSD.double)))
            g.add((qv, QUDT.unit, UNIT.KiloGM))
            g.add((iri, FUT.itemMass, qv))

    stmt_source = f"{prov.get('source', '')}|{doc.get('id') or ''}"
    # GROUPED SHAPE: one futuram:CompositionStatement per WHOLE, carrying N
    # futuram:PartRelation (one per constituent, refersTo its part + its quantity).
    # Identity (contentHash) is stamped LATER by the identity plugin; IRI deterministic.
    by_whole = {}
    for s in doc["statements"]:
        if s["whole"] in omit_nodes or s["part"] in omit_nodes:
            continue                      # references an omitted remainder node
        by_whole.setdefault(s["whole"], []).append(s)

    for whole, stmts in by_whole.items():
        cs = EX[f"comp_{whole}"]
        g.add((cs, RDF.type, FUT.CompositionStatement))
        g.add((EX[whole], FUT.hasCompositionStatement, cs))   # whole -> composition (ceon:hasComposition)
        g.add((cs, CEONP.compositionOf, EX[whole]))           # composition -> whole (CEON back-link)
        if full_metadata:
            g.add((cs, FUT.hasValidityPeriod, period))
            g.add((cs, PROV.hadPrimarySource, src))           # legacy provenance
            g.add((cs, PROV.wasAttributedTo, agent))

        for s in stmts:
            si = _stmt_iri(_stmt_view(s), stmt_source)        # author the relation IRI
            g.add((si, RDF.type, FUT.PartRelation))
            g.add((cs, FUT.hasPartRelation, si))
            g.add((si, FUT.refersTo, EX[s["part"]]))
            # NB: identity (futuram:contentHash, and skolemization of blank relations)
            # is the IDENTITY PLUGIN's job, NOT the ETL's. The emitter authors the
            # relation and STOPS; it never stamps contentHash. (authoring != identity)
            unit = UNIT_BY_NAME[s["unit"]]
            q = BNode(); g.add((q, RDF.type, CEONQ.QuantityInterval))
            emit = [(FUT.hasBestValue, float(s["best"]))]
            if s["dist"] in _HARD_LOWER:
                emit += [(CEONQ.hasMinimalValueIncludedOfInterval, float(s.get("lo", s["best"]))),
                         (CEONQ.hasMaximalValueIncludedOfInterval, float(s.get("hi", s["best"])))]
            for prop, v in emit:
                qv = BNode(); g.add((qv, RDF.type, QUDT.QuantityValue))
                g.add((qv, QUDT.numericValue, Literal(float(v), datatype=XSD.double)))
                g.add((qv, QUDT.unit, unit)); g.add((q, prop, qv))
            dcls, dparams = _distribution(s)
            dist = BNode(); g.add((dist, RDF.type, FUT[dcls]))
            for pname, pval in dparams.items():
                g.add((dist, FUT[pname], Literal(pval, datatype=XSD.double)))
            # RECTANGULAR: store NO numeric half-width; declare the strategy that
            # DERIVES it from the statement's DQ scores (futuram:FuturamDQS). Only the
            # DQ indicators + strategy are stored; limit/sigma/lo/hi are computed.
            if dcls == "RectangularDistribution":
                g.add((dist, FUT.uncertaintyLimitStrategy, FUT.FuturamDQS))
                lim_override = s.get("uncertainty_limit")
                if lim_override is not None:
                    g.add((dist, FUT.uncertaintyLimit,
                           Literal(float(lim_override), datatype=XSD.double)))
            g.add((q, FUT.hasDistribution, dist))
            g.add((si, FUT.hasQuantity, q))                   # quantity ON THE RELATION
            if full_metadata:
                g.add((si, FUT.hasProduction, proc))          # per-edge production
                for dim, score in (s.get("quality") or {}).items():
                    qm = BNode(); g.add((qm, RDF.type, DQV.QualityMeasurement))
                    g.add((qm, DQV.value, Literal(float(score), datatype=XSD.double)))
                    g.add((qm, DQV.isMeasurementOf, FUT[f"{dim}Score"]))
                    g.add((si, DQV.hasQualityMeasurement, qm))
    return g

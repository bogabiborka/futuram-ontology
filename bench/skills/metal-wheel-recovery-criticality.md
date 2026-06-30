---
name: metal-wheel-recovery-criticality
description: Answer how recoverable an element is in recycling, what recovery process applies, or whether an element is a CRITICAL / STRATEGIC raw material, using the Metal-Wheel and criticality overlays — both keyed to elements through ChEBI, not to the domain element classes directly. FETCH this for any "critical raw material", "strategic", "CRM", "recoverable", "recovery process", or "recyclability" question.
---

# Skill — CRITICAL / STRATEGIC raw materials + recovery

For questions about how RECOVERABLE an element is in recycling, what recovery
PROCESS applies, or whether an element is a CRITICAL / STRATEGIC raw material, use
the Metal-Wheel overlay. Like categories, this data is keyed to elements through
**ChEBI**, not to the FutuRaM element classes directly.

## How the overlays link to an element
Both overlays hang off the element's ChEBI class — you reach them by traversing
`rdfs:subClassOf` from the FutuRaM element to its ChEBI class, then following the
overlay's own properties:
- Recovery (Metal-Wheel): the element's ChEBI class carries
  `mw:hasRecoveryInformation ?ri` — ONE `?ri` record per recovery route. Each `?ri`
  carries `mw:hasBaseMetal ?baseMetal` (the base metal of THAT route),
  `mw:hasExpectedRecoveryEffectiveness ?eff`, `mw:hasRecoveryProcess ?proc`,
  `mw:foundIn ?fraction`. So an element's recovery facts =
  `futuram:<Element> rdfs:subClassOf ?chebi`, then
  `?chebi mw:hasRecoveryInformation [ mw:hasBaseMetal ?baseMetal ; … ]`.
  DIRECTION MATTERS: hang the records off the element's OWN ChEBI via
  `hasRecoveryInformation`. Do NOT write `?e mw:hasBaseMetal <thisElementChebi>` —
  that finds the OTHER elements for which THIS element is the base metal (the
  reverse relation), not how this element is recovered.
- Criticality: a ChEBI class carries `crit:remark ?flagClass`, and the flag class
  carries `crit:importance` (CRITICAL / STRATEGIC) and `crit:year`. So an element
  is critical iff its ChEBI class has a `crit:remark`.

Prefixes:
```
PREFIX mw:   <https://purl.org/metalwheel#>
PREFIX crit: <http://purl.org/futuram/criticality#>
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
```

## Is an element a critical raw material?
```sparql
ASK {
  futuram:<Element> rdfs:subClassOf ?chebi .
  FILTER(CONTAINS(STR(?chebi), "CHEBI"))
  ?chebi crit:remark ?flag .
}
```
For the IMPORTANCE (critical vs strategic) follow the flag:
```sparql
SELECT ?importance ?year WHERE {
  futuram:<Element> rdfs:subClassOf ?chebi . FILTER(CONTAINS(STR(?chebi),"CHEBI"))
  ?chebi crit:remark ?flag .
  ?flag crit:importance ?importance ; crit:year ?year .
}
```

## Recovery effectiveness / process of an element
```sparql
SELECT ?baseMetal ?eff ?proc WHERE {
  futuram:<Element> rdfs:subClassOf ?chebi . FILTER(CONTAINS(STR(?chebi),"CHEBI"))
  ?chebi mw:hasRecoveryInformation ?ri .
  ?ri mw:hasBaseMetal ?baseMetal ;
      mw:hasExpectedRecoveryEffectiveness ?eff ;
      mw:hasRecoveryProcess ?proc .
}
```
Recovery is reported per CONTEXT (where the element ends up, `mw:foundIn`), so one
element has SEVERAL `?ri` records with different base metals / effectiveness —
return them per context, or summarise; do not collapse to a single value unless the
question asks for one.

## "How can <Element> be recovered?" — the ROUTES it is recovered through
The recovery records partition by `mw:hasExpectedRecoveryEffectiveness`. Keep only
the routes where the element is genuinely recovered, and read its OWN route vs the
routes where it rides along with another base metal off the SAME effectiveness flag:
- `mw:TargetedElement` — the element's OWN route: here `?baseMetal` is the element
  itself, and `?proc` is its primary recovery process.
- `mw:MainlyRecoveredElement` — the element is an ACCOMPANYING recovered metal in
  the recovery of a DIFFERENT base metal (`?baseMetal` ≠ the element); `?proc` is
  that base metal's process.
Drop the `…Lost…` / `RecoveredInAlloyCompoundOrLost` records (not actually
recovered). Answer with the route's **base-metal CLASS IRI** (the `?baseMetal`
ChEBI class), not a bare metal name:
```sparql
SELECT DISTINCT ?baseMetal ?proc ?eff WHERE {
  futuram:<Element> rdfs:subClassOf ?chebi . FILTER(CONTAINS(STR(?chebi),"CHEBI"))
  ?chebi mw:hasRecoveryInformation ?ri .
  ?ri mw:hasBaseMetal ?baseMetal ;
      mw:hasRecoveryProcess ?proc ;
      mw:hasExpectedRecoveryEffectiveness ?eff .
  FILTER(?eff IN (mw:TargetedElement, mw:MainlyRecoveredElement))
}
```

## Combine with composition
"Which critical metals are in <whole>" = the constituents of the whole FILTERED to
those whose ChEBI class has a `crit:remark`. Resolve the whole and its
constituents as usual for this endpoint, then AND
in the criticality ASK above. Report the matching element names (and amounts if the
question asks for them).

### When the recovered material is a COMPONENT inside a SPECIFIC whole — use the SCOPE NODE
A recovery/recycling question is often scoped to a component inside one named whole
(e.g. "the recycling routes for <component> in <a specific product>"). The recoverable
quantity is then the component's element mass IN THAT WHOLE — which is NOT the
component's bare class. Do NOT read `fq:itemMass` / `fq:contains` off the component
CLASS node (e.g. a `<...>_Y<year>` class IRI): that is the global average across every
whole, and its itemMass is wrong for the specific product — every amount comes out
uniformly off by the itemMass ratio (a wrong-subject error that looks like a small
arithmetic miss). Instead bind the CONTEXTUAL SCOPE NODE — the node that
`futuram:partOf` the resolved specific whole and is `rdf:type` the component class —
and read ITS `fq:itemMass` and `fq:contains`. Get the exact shape (and the EXACT
`rdf:type`, never `subClassOf*`, to avoid stacking) from `component-in-whole-fq`, then
AND in the criticality / recovery joins above on each constituent.

### Recovery routes per CRM in a scoped component — full metal-wheel table
When the question asks for recovery routes of CRM constituents in a specific
component, return **one row per (element, base-metal)** — the full metal-wheel
table for each CRM. Do NOT filter by co-presence or collapse to one route per
element. Join the route directly off the element's own ChEBI class:

```sparql
PREFIX fq:      <https://www.purl.org/futuram/query#>
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX rdf:     <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
PREFIX crit:    <http://purl.org/futuram/criticality#>
PREFIX mw:      <https://purl.org/metalwheel#>
SELECT DISTINCT ?key (?m*?f AS ?kg) (?ru*?m*?f AS ?unc) ?baseMetal ?route WHERE {
  ?node futuram:partOf <WholeIRI> ;
        rdf:type <ComponentClass> ;
        fq:itemMass ?m ;
        fq:contains [ fq:constituent ?key ; fq:amount ?f ; fq:relativeUncertainty ?ru ] .
  ?key rdfs:subClassOf ?chebi . FILTER(CONTAINS(STR(?chebi), "CHEBI"))
  ?chebi crit:remark ?flag .
  # Full metal-wheel table for this element — no co-presence filter
  ?chebi mw:hasRecoveryInformation ?ri .
  ?ri mw:hasBaseMetal ?baseChebi ;
      mw:hasExpectedRecoveryEffectiveness ?eff ;
      mw:hasRecoveryProcess ?route .
  FILTER(?eff IN (mw:TargetedElement, mw:MainlyRecoveredElement))
  # Resolve baseChebi → futuram class IRI (the direct subClass, not a subsubclass)
  ?baseMetal rdfs:subClassOf ?baseChebi . FILTER(CONTAINS(STR(?baseChebi), "CHEBI"))
  FILTER NOT EXISTS {
    ?baseMetal rdfs:subClassOf ?mid . ?mid rdfs:subClassOf ?baseChebi .
    FILTER(?mid != ?baseMetal)
  }
} ORDER BY ?key ?baseMetal
```

`?baseMetal` is a `futuram:` class IRI (e.g. `futuram:<ElementClass>`) resolved from the
ChEBI base-metal. `?kg` and `?unc` repeat across base-metal rows for the same
element — that is correct (the element's mass in the component is constant; only
the recovery context varies).

## Notes
- Always go through the element's ChEBI class — there is no direct FutuRaM→
  Metal-Wheel link.
- `crit:remark` present = flagged at all; read `crit:importance` to distinguish
  CRITICAL from STRATEGIC, and `crit:year` for the assessment year.
- Effectiveness/criticality are CLASSES (categorical), not numbers — list or count
  them; do not invent a numeric score.

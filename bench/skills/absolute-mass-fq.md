---
name: absolute-mass-fq
description: Convert an fq kg/kg composition fraction into absolute kilograms by multiplying the fraction by the whole's item mass, with the multiply done inside the SPARQL query. Use on the fq endpoint whenever a question asks "how many kilograms / how much mass / the total demand" rather than a share — both the fraction and the item mass sit on the class, no tree-walking needed.
metadata:
  backends: fq
---

# Skill — absolute kilograms (itemMass × fraction)
<!-- backends: fq -->

Amounts are kg/kg FRACTIONS already aggregated on the class. To answer "how many
kilograms of X are in one item of Y", multiply the fraction by the whole's item
mass — both are on the class, no tree-walking needed.

## DO THE MULTIPLY IN SPARQL — never by hand
**Always let the endpoint compute `?frac * ?itemMass` with a projection
expression (`AS ?absoluteKg`) and report THAT number.** Do NOT fetch the bare
`fq:amount` fraction and then multiply it yourself in your head or in the answer.

Why this is mandatory: trace constituents come back in SCIENTIFIC NOTATION —
e.g. silver `1.2854E-5`, nitrogen `5.425E-6`. Multiplying such magnitudes by a
~2000 kg item mass by hand is exactly where answers go wrong: the big elements
look right but the tiny ones come out 1.5–2.5× off because the exponent gets
mishandled. The SPARQL engine multiplies them exactly; you cannot, reliably. If
you ever catch yourself reading a fraction and doing the arithmetic in prose,
STOP and rewrite the query with the `AS ?absoluteKg` form so the engine does it.

```sparql
PREFIX fq:      <https://www.purl.org/futuram/query#>
PREFIX futuram: <https://www.purl.org/futuram#>
SELECT (?kgPerItem * ?frac AS ?absoluteKg) WHERE {
  <ClassIRI> fq:itemMass ?kgPerItem ;
             fq:contains [ fq:constituent <ConstituentIRI> ; fq:amount ?frac ] .
}
```

To list EVERY constituent of a kind (element / material / component) with its
absolute mass, filter the constituent by `rdfs:subClassOf*` its base class:
```sparql
PREFIX fq:      <https://www.purl.org/futuram/query#>
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?constituent (?kgPerItem * ?frac AS ?absoluteKg) WHERE {
  <ClassIRI> fq:itemMass ?kgPerItem ;
             fq:contains [ fq:constituent ?constituent ; fq:amount ?frac ] .
  ?constituent rdfs:subClassOf* <BaseClassIRI> .   # e.g. the Element / Material base
}
ORDER BY DESC(?absoluteKg)
```
Report absolute results in kg (the harness also accepts g / tonne and converts).

---
name: read-amount-and-units
description: Read a composition amount together with its unit from the reified quantity node on the baseline composition endpoint, and normalise mixed units before combining. Use whenever you read amounts on the baseline composition endpoint — always pull the unit alongside the value so kg/kg vs g/kg (etc.) are reconciled.
metadata:
  backends: composition
---

# Skill — read a composition amount WITH its unit, and normalise
<!-- backends: composition -->

On the `composition` endpoint the amount lives in a reified quantity node. Always
read the unit alongside the value.

```sparql
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX qudt: <http://qudt.org/schema/qudt/>
SELECT ?part ?best ?unit WHERE {
  ?whole futuram:hasCompositionStatement ?stmt .
  ?stmt  futuram:hasPartRelation ?pr .
  ?pr    futuram:refersTo ?part ;
         futuram:hasQuantity ?qi .
  ?qi futuram:hasBestValue [ qudt:numericValue ?best ; qudt:unit ?unit ] .
}
```

The quantity is a `QuantityInterval`: the point estimate is `futuram:hasBestValue`
(read above). Its spread is NOT a stored min/max — it is a `futuram:hasDistribution`
(a rectangular distribution whose half-width is DERIVED from the part's data-quality
scores). To turn that into a ± value, see the `uncertainty-composition` skill.

UNITS ARE MIXED — do not assume a single unit. The `?unit` is a QUDT unit IRI.
Before ANY arithmetic, convert every amount to ONE common base:

1. Read the distinct `?unit` IRIs actually present (the VoID's unit inventory
   lists them, or `SELECT DISTINCT ?unit` over the statements).
2. For each, apply that QUDT unit's standard conversion factor to a common base
   (mass-fractions → a dimensionless ratio; absolute masses → kilograms). A
   "per-mille"/"grams-per-kilogram"-style fraction unit is 1/1000 of a
   "kg-per-kg" ratio; a gram is 1/1000 of a kilogram; etc. — i.e. use the unit's
   own definition, not a hardcoded IRI.
3. Then compare / multiply / sum.

Branch on the `?unit` you actually observed (do not hardcode one specific unit
IRI — handle each present unit by its factor), e.g.:

```sparql
BIND( ?best * ?factor AS ?normalised )   # ?factor from the unit's definition
```

Never compare or multiply two amounts without first putting them in the same
base unit. itemMass is an absolute mass (kilograms), not a fraction — keep it
separate from kg/kg fractions.

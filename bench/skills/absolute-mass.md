---
name: absolute-mass
description: Convert a composition kg/kg fraction into absolute kilograms by multiplying the fraction by the whole's item mass. Use on the composition (raw) endpoint when a question asks "how many kilograms of X are in one item of Y" — the item mass is a QUDT value in kilograms on the instance, typed up to its named base class.
metadata:
  backends: composition
---

# Skill — absolute kilograms (itemMass × fraction)
<!-- backends: composition -->

Composition amounts are kg/kg FRACTIONS. To answer "how many kilograms of X are
in one item of Y", multiply the fraction by the whole's item mass.

`itemMass` is on the INSTANCE as a QUDT value in kilograms. Each individual is
typed up to its named base class (`<BaseClassIRI>` — the Product/Component kind),
so scope the instances by that base class directly:
```sparql
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX qudt: <http://qudt.org/schema/qudt/>
SELECT ?inst ?kg WHERE {
  ?inst a <BaseClassIRI> ;                                  # the named base kind
        futuram:itemMass [ qudt:numericValue ?kg ; qudt:unit ?u ] .
}
```
Or pin a single named class with `?inst a <ClassIRI>` for one specific kind. Read
the unit off `?u` and convert by its own factor (see `read-amount-and-units`);
do not assume the unit. Multiply by the aggregated kg/kg fraction you computed
(see `aggregate-multihop`).
Only the Product/Component base kinds carry `itemMass`; the Material/Element
kinds do not (matter is bulk, measured per kilogram, never counted).

Report absolute results in kg (the harness also accepts g / tonne and converts).

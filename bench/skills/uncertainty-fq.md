---
name: uncertainty-fq
description: Read and report the ± uncertainty of an amount on the fq endpoint, where each served amount node carries a relative-uncertainty fraction (not an absolute value) you convert to absolute by multiplying through. Use when an fq question asks for a ± value, "give the uncertainty", or an "absolute uncertainty in the same unit".
metadata:
  backends: fq
---

# Skill — the ± uncertainty of an amount (fq endpoint)
<!-- backends: fq -->

When the question asks for the ± UNCERTAINTY of a reported quantity (a "± value",
"give the uncertainty", "absolute uncertainty same unit"), the fq view already
serves it. Each served amount node (`fq:Amount`, reached via `fq:contains`) carries
a **`fq:relativeUncertainty`** — a fraction of the amount, NOT an absolute value.

## The rule: absolute ± = relativeUncertainty × absolute amount
`fq:relativeUncertainty` is RELATIVE (e.g. 0.10 means ±10%). To report the ±
uncertainty in the SAME UNIT as the answer (kg), multiply it by the ABSOLUTE
amount you computed (itemMass × fraction, per `absolute-mass-fq`):

```
absolute_uncertainty_kg  =  relativeUncertainty  ×  (itemMass × fraction)
```

Do the multiply IN the SPARQL query (never by hand). Bind the amount node's
fraction (`fq:amount`), its `fq:relativeUncertainty`, and the class's `fq:itemMass`
in ONE query, and project `itemMass × fraction` (the absolute value) AND
`itemMass × fraction × relativeUncertainty` (the absolute ±):

```sparql
PREFIX fq: <https://www.purl.org/futuram/query#>
SELECT ?constituent
       (?frac * ?itemMass AS ?kg)
       (?relUnc * ?frac * ?itemMass AS ?kgUncertainty)
WHERE {
  <ClassIRI> fq:itemMass ?itemMass ;
             fq:contains [ fq:constituent ?constituent ;
                           fq:amount ?frac ;
                           fq:relativeUncertainty ?relUnc ] .
  # FILTER(?constituent = <ConstituentIRI>)   # add to scope to one constituent
}
```

## Combining the ± over SEVERAL constituents (a TOTAL) — RSS, not a plain sum
When the answer is a TOTAL mass over several constituents (e.g. "total critical raw
material content"), you sum their masses — but you do **NOT** linearly add their
uncertainties. Independent uncertainties combine in **quadrature (Root-Sum-of-
Squares)**: the total's absolute ± is

```
sigma_total  =  sqrt( Σ sigma_i^2 )        where sigma_i = relUnc_i × (itemMass × frac_i)
```

A plain `SUM(sigma_i)` OVER-estimates the total ± (it assumes every error pushes the
same way) and is WRONG. Standard SPARQL 1.1 has no `SQRT`, but this endpoint (Apache
Jena/Fuseki) provides one as an ARQ extension function, `afn:sqrt`, so you CAN do the
whole RSS in ONE query: `GROUP BY`, sum each `sigma_i^2`, and wrap the sum in
`afn:sqrt`. De-duplicate the constituent rows first (the DISTINCT subquery) so no
constituent's sigma² is counted twice:

```sparql
PREFIX fq:  <https://www.purl.org/futuram/query#>
PREFIX afn: <http://jena.apache.org/ARQ/function#>
SELECT (SUM(?kg) AS ?totalKg) (afn:sqrt(SUM(?sigma * ?sigma)) AS ?totalUncertaintyKg)
WHERE {
  SELECT DISTINCT ?constituent ?kg ?sigma WHERE {
    <ClassIRI> fq:itemMass ?itemMass ;
               fq:contains [ fq:constituent ?constituent ;
                             fq:amount ?frac ;
                             fq:relativeUncertainty ?relUnc ] .
    # restrict ?constituent to the set you are totalling (e.g. a kind membership)
    BIND(?frac * ?itemMass AS ?kg)
    BIND(?relUnc * ?frac * ?itemMass AS ?sigma)
  }
}
```
`?totalUncertaintyKg` is then the RSS ± in kg, computed entirely in the query. (If an
endpoint ever lacks `afn:sqrt`, fall back to projecting `SUM(?sigma*?sigma)` and taking
the square root of that one number yourself.)

## Notes
- Report the value AND its ± in the same unit (kg): `<value> ± <absUncertaintyKg>`.
- `fq:relativeUncertainty` lives on the AMOUNT node (the `fq:contains` object), not
  on the class — join through `fq:contains` to reach it.
- If a question asks for the relative/percent uncertainty instead, report
  `fq:relativeUncertainty` directly (×100 for a percentage).
- The amount node may also carry `fq:meanDataQuality` / `fq:uncertaintyMethod` /
  `fq:dqs` describing HOW the uncertainty was derived — read them only if the
  question asks about the method, not the number.
- Never invent or hand-estimate an uncertainty: if `fq:relativeUncertainty` is
  absent for an amount, say so rather than guessing.

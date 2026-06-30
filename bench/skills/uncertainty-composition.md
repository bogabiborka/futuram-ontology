---
name: uncertainty-composition
description: Calculate the ± uncertainty of an amount on the composition endpoint, where it is NOT pre-computed — each part's quantity carries a rectangular distribution whose half-width is derived from the part's data-quality scores by an uncertainty ruleset, so you compute the relative-then-absolute uncertainty yourself in stages. Use when a composition question asks for a ± value / the uncertainty / an absolute uncertainty in the same unit.
metadata:
  backends: composition
---

# Skill — CALCULATE the ± uncertainty of a composition amount (composition endpoint)
<!-- backends: composition -->

On this endpoint the ± uncertainty is **NOT pre-computed** — there is no stored
uncertainty value to read. Instead each part's quantity carries a
`futuram:hasDistribution` that is a `futuram:RectangularDistribution` whose half-width
is **derived from the part's data-quality (DQ) scores** by an uncertainty ruleset. So
you compute it yourself, in the stages below. The relative uncertainty is a FRACTION
(a σ); multiply by the absolute amount for a ± in kg.

Throughout, `<PartRelation>` is a part relation reached as
`?whole futuram:hasCompositionStatement ?stmt . ?stmt futuram:hasPartRelation ?pr`.

## The DQ scores live on the part relation
Each part relation carries six per-dimension DQ scores via `dqv:hasQualityMeasurement`
(Accuracy, Completeness, Consistency, Integrity, Timeliness, Validity — each a score,
typically 1–3, where lower is better):

```sparql
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX dqv: <http://www.w3.org/ns/dqv#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?pr ?dimLabel ?score WHERE {
  ?whole futuram:hasCompositionStatement ?stmt .
  ?stmt  futuram:hasPartRelation ?pr .
  ?pr    dqv:hasQualityMeasurement ?qm .
  ?qm    dqv:isMeasurementOf ?metric ; dqv:value ?score .
  OPTIONAL { ?metric rdfs:label ?dimLabel }
}
```

## Stage 1 — DQ scores → an uncertainty LIMIT (per part relation)
The ruleset turns the six scores into a single rectangular **limit** by a
WEIGHTED SUM of the scores, then a BAND lookup. The per-dimension weights and the
weighted-sum→limit band table are DATA in the `futuram:UncertaintyRuleset` (read them
from the VoID / TBox rather than assuming — query `futuram:hasDimensionWeight`
(`weightDimension`,`weightValue`) and `futuram:hasLimitBand`
(`weightedSumLowerBound`,`weightedSumUpperBound`,`bandUncertaintyLimit`)).

The method, with the ruleset's current values:

1. **Weighted sum** of the six scores. Most dimensions weigh 1.0; the ruleset
   currently down-weights Integrity (0.5) and up-weights Timeliness (1.5):
   `wsum = Σ (weight_dim × score_dim)` over all six dimensions. ALL six must be
   present — the rule is calibrated for the complete vector.
2. **Band the weighted sum to a limit** (half-open `[lo, hi)`). Current bands:
   `wsum<7 → 0.10`, `7–9 → 0.15`, `9–10 → 0.20`, `10–14 → 0.25`, `14–15 → 0.30`,
   `≥15 → 0.35`.

## Stage 2 — limit → relative uncertainty σ (rectangular)
A rectangular distribution's standard uncertainty is its half-width over √3:

```
σ_relative  =  limit / sqrt(3)
```

This σ is the FRACTION (1-to-1 with the source CSV's uncertainty% ÷ 100). Do the
`/ sqrt(3)` in SPARQL (`?limit / 1.7320508`) — never by hand.

## Stage 3 — combine several part relations (Eq.3) → one amount's σ
A constituent's amount on a whole usually comes from SEVERAL part relations (parallel
paths / several statements). Combine their relative σ's by the ruleset's
`combinationMethod` (currently `RootSumOfSquares`), **contribution-weighted by each
statement's best value** `v` (the kg/kg the part relation states, via
`futuram:hasQuantity → futuram:hasBestValue → qudt:numericValue`):

```
σ_aggregate  =  sqrt( Σ (σ_i × v_i)^2 )  /  Σ v_i
```

i.e. take the absolute σ·v of each contributing statement, root-sum-square them, and
divide by the total value. If there is exactly one contributing statement this reduces
to that statement's σ.

## Stage 4 — absolute ± in kg
`σ_aggregate` is relative. To report the ± in the answer's unit:

```
absolute_uncertainty_kg  =  σ_aggregate  ×  (itemMass × fraction)
```

where `itemMass × fraction` is the absolute amount you computed (see `absolute-mass`).
Do every multiply IN the query.

## Stage 5 — a TOTAL over several constituents: combine by RSS, not a plain sum
When the answer is a TOTAL mass over several constituents (e.g. "total critical raw
material content"), sum their masses — but combine their absolute ± in **quadrature
(Root-Sum-of-Squares)**, the SAME method used within a constituent: independent errors
do NOT add linearly.

```
sigma_total  =  sqrt( Σ sigma_k^2 )     over the k constituents in the total
```

A plain `SUM(sigma_k)` over-estimates and is WRONG. Standard SPARQL 1.1 has no `SQRT`,
but this endpoint (Apache Jena/Fuseki) provides the ARQ extension `afn:sqrt`, so you
can do the whole RSS in ONE query: `GROUP BY`, sum each `sigma_k^2`, and wrap the sum
in `afn:sqrt` (declaring `PREFIX afn: <http://jena.apache.org/ARQ/function#>`):

```
(afn:sqrt(SUM(?sigma * ?sigma)) AS ?totalUncertainty)   (?sigma = each constituent's absolute ±)
```

De-duplicate the constituent rows first (a DISTINCT subquery) so no constituent's
sigma² is counted twice. If an endpoint lacks `afn:sqrt`, project `SUM(?sigma*?sigma)`
and take the square root of that one number yourself.

## Notes
- DON'T assume the band numbers / weights are fixed — they are the ruleset's DATA;
  read them from the VoID/TBox when you can, and only fall back to the values above.
- The descriptive **mean data quality** and **DQS** (a 1–4 grade) are a SEPARATE
  recompute (mean of the scores → a DQS band); they describe HOW good the data is, not
  the ± itself. Report them only if the question asks about data quality, not the ±.
- Never invent a ± when the DQ scores are absent — say the data does not support it.

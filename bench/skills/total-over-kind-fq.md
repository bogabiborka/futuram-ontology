---
name: total-over-kind-fq
description: Total or list "everything of a KIND" inside one whole on the fq endpoint — the constituents of a fixed whole filtered to a family/category and summed (or listed). The members of the kind are the transitive rdfs:subClassOf* closure of a family class, discovered from the data, never hand-listed. Use for "the total X content", "how much of family F", "list the F in W", "all the grades of M" — where the WHOLE is fixed and you range over a kind of CONSTITUENT.
metadata:
  backends: fq
---

# Skill — TOTAL/LIST over a KIND of constituent (subClassOf* closure) — fq endpoint
<!-- backends: fq -->

The whole is FIXED (one class you resolved); you want its constituents that belong to
a KIND — a material family, a category of element, a group of components — totalled or
listed. The members of that kind are NOT a list you type: they are the transitive
`rdfs:subClassOf*` closure of one family/category class, and the data defines exactly
which ones the whole contains.

## The shape
Read the whole's `fq:contains` and KEEP only constituents under the family class, via
the transitive closure. Resolve the whole and the family class ONCE (see
`resolve-class`), then:

```sparql
PREFIX fq:      <https://www.purl.org/futuram/query#>
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?key (?m * ?f AS ?kg) WHERE {
  <WholeClassIRI> fq:itemMass ?m ;
      fq:contains [ fq:constituent ?key ; fq:amount ?f ] .
  ?key rdfs:subClassOf* <FamilyClassIRI> .          # keep only members of the kind
}
ORDER BY DESC(?kg)
```

For a single TOTAL, wrap it: `SELECT (SUM(?m * ?f) AS ?total) WHERE { … }` (de-dupe
the constituent rows in a `SELECT DISTINCT` subquery first if a constituent could match
the closure more than once, so no `(constituent, amount)` is summed twice).

- **Transitive `rdfs:subClassOf*`, NOT a single hop.** Members attach several levels
  below the family (intermediate process/grade classes sit between), so one hop misses
  the deeper ones. The intermediates that carry no `fq:amount` simply contribute no row.
- **The remainder is ALREADY in the closure.** `futuram:unknownElement rdfs:subClassOf*
  futuram:Element` is `true` (likewise the other unknown kinds under their parents), so
  a single `?key rdfs:subClassOf* futuram:Element` already includes the unattributed
  remainder. NEVER add a separate `UNION { VALUES ?key { futuram:unknownElement } }` —
  it then matches both branches and a `SUM` double-counts it.
- **A CATEGORY may live in a bridged ontology, not the futuram hierarchy.** A chemistry
  category (metals, rare-earth, …) is reached through the element's ChEBI class — see
  `chebi-element-classification` / `metal-wheel-recovery-criticality`; the closure leg
  then runs up the ChEBI taxonomy (`?key rdfs:subClassOf* <ChebiCategoryIRI>`). If the
  question scopes the total to a COMPONENT inside a specific whole rather than to a
  top-level whole, get `chebi-element-classification` — step 3 has the right shape for
  that case.
- Multiply `fq:itemMass * fq:amount` for absolute kg INSIDE the query (see
  `absolute-mass-fq`).
- **RESOLVE TO THE FAMILY, not the literal word.** The question's wording may add a
  qualifier (an adjective in front of the family name) that names only ONE sub-branch,
  yet the expected answer is the WHOLE family of types, of which that branch is just
  one. Do NOT resolve to the single class the qualifier names and stop — that returns
  one row and misses the siblings. Resolve to the FAMILY class whose `rdfs:subClassOf*`
  closure covers every type (read the candidates' rdfs:comment to find the umbrella,
  per `resolve-class`), then let the closure return all of them. If your result has
  FEWER rows than the question's "distribution"/"types" framing implies, you resolved a
  child instead of the family — go UP to the family and re-run.

This is the SIBLING of `per-subclass-breakdown-fq`: there the subclass is the SUBJECT
and you report each DIRECT child on its own row (`subClassOf`); here the subclass is the
CONSTITUENT of a fixed whole and you fold the WHOLE closure into one answer
(`subClassOf*`).

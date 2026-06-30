---
name: aggregate-multihop
description: Compute the total amount of a constituent that sits several hops below a whole (instance → component → material → element) on the baseline composition endpoint, which stores no aggregate. Use when the element/material asked about is not a direct part of the whole and you must traverse the statement tree and sum the contributions yourself.
metadata:
  backends: composition
---

# Skill — aggregate a constituent across the composition tree (composition endpoint)
<!-- backends: composition -->

The constituent a user asks about is usually several hops below the whole:
`instance → component → material → element`. The `composition` endpoint stores
NO aggregate — you must compute it yourself. In the queries, `<TERM>` is the
user's class term, `<ConstituentIRI>` is the target constituent class (e.g. the
element asked about).

## The math
- Down ONE path: multiply the (unit-normalised) fractions of each hop.
- Across PARALLEL paths to the same constituent: sum the per-path products.
- Across the INSTANCES of a class (several instances, or year slices): take the
  mean (per the class's `futuram:hasAggregationStrategy`; equal mean unless the
  strategy says otherwise).

## Method
1. Resolve the user's term to a class IRI, then find its instances. Each
   individual is typed directly to every class up to its named base, so once you
   have the class IRI you can bind instances with a plain `?inst a <ClassIRI>`
   (no property path needed). Resolve the term by the class **`rdfs:label` and
   `rdfs:comment`** — the IRI is OPAQUE, so search the meaning, never the IRI string:
   ```sparql
   SELECT DISTINCT ?cls ?label ?comment WHERE {
     ?cls a owl:Class .
     OPTIONAL { ?cls rdfs:label   ?label }
     OPTIONAL { ?cls rdfs:comment ?comment }
     FILTER( CONTAINS(LCASE(STR(?label)),   "<TERM>")
          || CONTAINS(LCASE(STR(?comment)), "<TERM>") )    # the MEANING, never the IRI
   }
   ```
   then `?inst a <ClassIRI> . ?inst futuram:hasCompositionStatement ?stmt .`
   (see also the `resolve-class` / `find-instances-of-class` skills.)
2. Pull the statement edges as (whole, part, value, unit) and normalise units
   (`read-amount-and-units`). One hop is
   `?whole futuram:hasCompositionStatement ?stmt . ?stmt
   futuram:hasPartRelation ?pr . ?pr futuram:refersTo ?part ;
   futuram:hasQuantity … .`
3. Identify the target constituent class (`<ConstituentIRI>`) — match the part
   instance's `rdf:type` to it.
4. Multiply along each path; sum parallel paths; mean across the class's instances.

## CRITICAL: scope `?whole` to the asked class's instances
The biggest mistake is leaving `?whole` unbound — then you sum copper paths over
the ENTIRE graph (every product, year, scenario) and get a meaningless number.
ALWAYS pin the top whole to an instance of the resolved class:
`?whole a <ClassIRI>` (every individual is typed to its class and to its named
base, so a plain `a` binds them; use `<BaseClassIRI>` to scope a whole kind).

## Fixed-depth example (one instance → mid → leaf), per instance
Each hop is `whole → hasCompositionStatement → stmt → hasPartRelation → pr →
refersTo → part`, and the middle part of hop 1 is the whole of hop 2.
```sparql
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX qudt: <http://qudt.org/schema/qudt/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?whole ?u1 ?v1 ?u2 ?v2 WHERE {
  ?whole a <ClassIRI> .                             # SCOPE: only the asked class
  ?whole futuram:hasCompositionStatement ?st1 .
  ?st1   futuram:hasPartRelation ?pr1 .
  ?pr1   futuram:refersTo ?mid ;
         futuram:hasQuantity/futuram:hasBestValue [ qudt:numericValue ?v1 ; qudt:unit ?u1 ] .
  ?mid   futuram:hasCompositionStatement ?st2 .
  ?st2   futuram:hasPartRelation ?pr2 .
  ?pr2   futuram:refersTo ?leaf ;
         futuram:hasQuantity/futuram:hasBestValue [ qudt:numericValue ?v2 ; qudt:unit ?u2 ] .
  ?leaf rdf:type <ConstituentIRI> .                 # the target constituent class
}
```
Keep `?whole` in the SELECT so you can aggregate PER instance, then take the
**mean across the instances** (that is the class-level value).

## The aggregation, step by step
1. Convert each hop value to the common base by its own unit's factor (see
   `read-amount-and-units`) — do NOT hardcode a unit IRI.
2. Down one path: MULTIPLY the per-hop fractions.
3. Across parallel paths within ONE instance: SUM the path products.
4. Across the instances of the class: take the MEAN (per the class's
   `futuram:hasAggregationStrategy`).

## Depth is NOT fixed
The whole→…→target chain can be deeper than two hops (product → component →
material → element). The 2-hop query above is only a template — check how deep
the real paths go (follow refersTo down each PartRelation until the part's
rdf:type is the target),
and add hops / run one query per depth. SPARQL 1.1 cannot do unbounded recursive
arithmetic in one query, so fix the depth you observe or pull the edges out and
aggregate in your own reasoning. If the tree is deeper/irregular than you can
cover within the step budget, report your best partial value and say it is
partial — do not invent a number.

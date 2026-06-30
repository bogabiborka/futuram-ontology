---
name: component-in-whole-fq
description: Read a component (or aggregate component class) scoped to ONE specific whole on the fq endpoint via its precomputed contextual scope node (the node that futuram:partOf the whole, carrying its own item mass and contains amounts) — not the component's global class average. Use when the question is "the components of whole W", "how is element E distributed across W's components", or "which component contributes most", and you need per-component in-context values.
metadata:
  backends: fq
---

# Skill — a component (or aggregate component class) inside a specific whole (fq endpoint)
<!-- backends: fq -->

A question may ask for a COMPONENT scoped to ONE specific whole — not the
component's global class average. The fq view precomputes a CONTEXTUAL SCOPE NODE
for exactly this: a node that `futuram:partOf` a specific whole class, carrying its
own `fq:itemMass` (the component's mass in THAT whole) and `fq:contains` amounts.
(The scope→whole link is `futuram:partOf` — the `futuram:` namespace, NOT `fq:`.)

## The scope node shape
The scope node is typed (`rdf:type`) to the component class it represents, carries
a `futuram:partOf` edge to the specific whole class it sits in, an `fq:itemMass`
(the component's kg in that whole), and `fq:contains` amount nodes — each amount
node carrying an `fq:constituent` and an `fq:amount` fraction.
Bind it by its `futuram:partOf` edge to the whole plus its component type — do NOT
use the bare component class (that is the global average over every whole, which
dilutes the context).

**When the question names a specific whole, do NOT use the component class IRI as
the query subject.** `<ComponentClassIRI> fq:itemMass ?m` reads the GLOBAL average
mass across all wholes and years — the wrong number whenever a specific whole is
named. In that case the scope node `?node` is the correct subject: reach it through
`?node futuram:partOf <WholeYearClassIRI> ; rdf:type <ComponentClassIRI>`, then
read `?node`'s own `fq:itemMass`. Use `?node` as both query subject AND label in
your ANSWER — its IRI encodes the whole+year context. The component class IRI is
only correct when the question genuinely asks for the cross-whole average.

## Resolve the whole and the component, then read the scoped amount
Resolve the user's whole term and component term to their class IRIs by
`rdfs:label` FIRST (see `resolve-class`, including picking the right specific class
over a broad aggregate). Then bind the scope node by its edges: its `futuram:partOf` to
the whole, its `rdf:type` to the component type, its `fq:itemMass`, and its
`fq:contains` amount node carrying the `fq:constituent` you asked about and its
`fq:amount` fraction. Scope the whole with a transitive `rdfs:subClassOf` path up
to the resolved whole class and the component type with a transitive
`rdfs:subClassOf` path up to the resolved component class, so the node is the right
component in the right whole. Project the `fq:amount` fraction times the
`fq:itemMass` as the absolute mass in the query.
Always do the fraction-times-itemMass multiply INSIDE the query (see
`absolute-mass-fq`) — never multiply by hand.

The shape of the query (resolve the holder/scope class `<ScopeClassIRI>`, the
component class `<ComponentIRI>`, and the constituent `<ConstituentIRI>` first):

```sparql
PREFIX fq:      <https://www.purl.org/futuram/query#>
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX rdf:     <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
SELECT (?frac * ?itemMass AS ?kg) WHERE {
  ?node futuram:partOf  <WholeClassIRI> ;          # scope -> the specific whole
                                                   # (its YEAR class if a year is named)
        rdf:type        <ComponentIRI> ;           # EXACT component class — see below
        fq:itemMass     ?itemMass ;                # the scope node's OWN itemMass
        fq:contains [ fq:constituent <ConstituentIRI> ; fq:amount ?frac ] .
}
```

**Bind the component type with EXACT `rdf:type <ComponentIRI>`, NOT
`rdfs:subClassOf*` — or you over-count.** The fq view stacks SEVERAL scope nodes for the
SAME component in the same whole: the base component class and each of its rollup-slice
classes get their OWN `…_in_<whole>` node, and EACH carries its own `fq:itemMass`. They
are the SAME mass expressed at different rollup levels — not different parts. So
`?node rdf:type ?c . ?c rdfs:subClassOf* <ComponentIRI>` matches every stacked node and a
`SUM` returns a multiple of the true value (e.g. 3× when three levels are present).
Resolve the component to its ONE class IRI and bind `rdf:type` to exactly that class, so
you read ONE node with ONE itemMass. (If you genuinely need a sub-component, resolve THAT
child's IRI and bind its exact type — never widen with `*`.)

Note the scope link is `futuram:partOf` — it lives in the `futuram:` namespace,
not the `fq:` query namespace. `fq:sliceOf` is a DIFFERENT edge (an aggregation-axis
rollup link); it points a slice at its base class, NOT at a holder, so it will not
scope a component to its whole.

## When the question names a YEAR, the WHOLE you scope to must be the YEAR class
Scope nodes are TIME-SCOPED: a component's scope node hangs off the whole's
year-specific class (e.g. `…partOf <WholeClassIRI>_Y<year>`), and that node carries
the itemMass + amounts FOR THAT YEAR. So when the question pins a year, the
`futuram:partOf` target must be the whole's YEAR class (resolve it per `resolve-class`
— the base whole is the all-years mean), NOT the bare base whole. Binding `partOf`
the base (un-year) whole returns ZERO rows. And do NOT confuse the two year IRIs:
- the WHOLE's year class — `<WholeClassIRI>_Y<year>` — is the correct `partOf` target;
- the COMPONENT's own year class — `<...Component...>_Y<year>` — is the global
  (all-wholes) average for that year; binding ITS itemMass instead of the scoped
  node's gives the wrong (uniformly off) mass.
Always: `?node futuram:partOf <WholeClassIRI>_Y<year> ; rdf:type <ComponentIRI>` and
read the scope node's own `fq:itemMass`.

## Aggregate component classes are single served classes (roll up their parts)
A parent aggregate component class is the COMPOSITION of its part sub-components: it is
`rdfs:subClassOf`-linked to each child, and its scope node's content is the
mass-weighted SUM of those parts. So "the <aggregate> in whole W" is ONE scope node
typed with the aggregate's class — you do NOT find the child components and sum them
yourself. Resolve the aggregate term to its class and ask that class directly.

If a question asks for the constituents "at the component level" of a whole, each
aggregate component class is one scope node `futuram:partOf` that whole — query them by
their resolved aggregate class, not by their leaves.

## Total over a whole, or a per-element list
Drop the component filter to get every component scope node of the whole, or
keep `fq:contains` on the whole class itself for the already-aggregated total.
For the unattributed remainder of an aggregate component class, its parts include an
unknown-kind holder — see the `fq` skill (the remainder is already in the
`rdfs:subClassOf` closure; never add it in twice).

## "Distribute element X across the components of whole W" — ONE fan-out query
A "how is <element> distributed across the components of <whole>" question wants ONE
ROW PER component of the whole, each with that element's kg in that component — a
breakdown you DISCOVER, never hand-list, and never get by splitting the whole's
aggregate fraction yourself. Let the scope-node fan-out enumerate the components;
keep ONLY the whole's DIRECT components (the ones one `rdfs:subClassOf` step below
`Component`, not their sub-components — else you double-count a parent and its parts).
Compute kg in-query (× itemMass), one row each:

```sparql
PREFIX fq:      <https://www.purl.org/futuram/query#>
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX rdf:     <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?node (?m * ?f AS ?kg) WHERE {
  ?node futuram:partOf <WholeClassIRI> ;     # the whole's YEAR class if a year is named
        rdf:type    ?key ;                   # the component class — a VARIABLE, discovered
        fq:itemMass ?m ;
        fq:contains [ fq:constituent <ConstituentIRI> ; fq:amount ?f ] .
  ?key rdfs:subClassOf <ComponentRootIRI> .  # the level-root component class
  FILTER NOT EXISTS {                          # keep DIRECT components only (no skips)
    ?key rdfs:subClassOf ?mid .
    ?mid rdfs:subClassOf <ComponentRootIRI> .
    FILTER(?mid != <ComponentRootIRI>)
  }
}
ORDER BY DESC(?kg)
```

**Use `?node` (the scope-node IRI) as the label in your ANSWER, NOT `?key` (the
component class IRI).** The scope-node IRI uniquely identifies the component scoped
to that specific whole and year (e.g. `fq:<ComponentClass>_in_<VehicleIRI>_Y<year>`);
the bare class IRI is the global average across all wholes and is the wrong identity
for a per-whole breakdown.

- Report kg, NOT the kg/kg fraction — multiply by `fq:itemMass` (see `absolute-mass-fq`).
- Report one row PER component; do NOT collapse the smaller ones into a single
  "other"/"remaining" bucket, and NEVER invent a holder class for them — a class you
  did not read from the data does not exist and scores wrong. If the whole genuinely
  has an unattributed remainder, it surfaces as the data's own unknown-kind holder
  (see the `fq` skill), not a name you make up.
- The component set is DISCOVERED via the scope fan-out — do not enumerate component
  IRIs in a VALUES block.

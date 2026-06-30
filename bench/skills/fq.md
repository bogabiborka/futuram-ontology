---
name: fq
description: How to answer a product/material/element composition question on the query-optimized fq: dataset (the /query endpoint), where amounts are already aggregated kg/kg fractions on classes. Use as the primary fq guide — reading a constituent's amount in one hop, the per-subclass breakdown (direct rdfs:subClassOf), totalling a whole kind via the rdfs:subClassOf closure, contains-vs-partOf, and the unattributed remainder.
metadata:
  backends: fq
---

# Skill — answering a composition question on the fq endpoint
<!-- backends: fq -->

You are querying the query-optimized `fq:` dataset (the `/query` endpoint).
Amounts are already aggregated kg/kg fractions on CLASSES — one pattern answers
almost everything. No tree traversal, no unit normalisation, no manual
aggregation. Replace `<TERM>` with the user's lowercased term and `<ClassIRI>` /
`<ConstituentIRI>` with the IRIs you resolve.

**READ THIS FIRST — the one habit that wins on this endpoint.** Almost every wrong or
timed-out answer here is the same mistake: a question ranges over the SUBCLASSES of a
class — total them, or report one number per subclass — and instead of letting the
data supply that set, the model hand-types the subclasses (a short, wrong list) or
fires one tiny query per subclass (100+ micro-queries until the clock runs out, never
an answer). DON'T. The subclasses of any class are the data's own `rdfs:subClassOf`
edges, so DISCOVER them in ONE query — never a `VALUES` list, never
one-query-per-subclass. Two shapes do this, and picking the right one matters (see the
"Ranging over SUBCLASSES" section below and the `per-subclass-breakdown-fq` /
`total-over-kind-fq` skills): a number PER subclass uses a DIRECT `rdfs:subClassOf`
hop; a TOTAL over a kind uses the `rdfs:subClassOf*` CLOSURE. Then STOP and write the
ANSWER line. If your first query is close but incomplete, FIX that one query — do not
start a scatter of little ones. The moment you are typing subclass names, or running
your fifth near-identical query, you have taken the wrong path: back out to one
`rdfs:subClassOf` query.

## 1. Resolve the user's term to a class IRI (if not obvious)

Every class carries an `rdfs:label` (its name) and most carry an `rdfs:comment`
(what it IS — a broad roll-up aggregate or a specific class). The class IRI is
OPAQUE, so discover by the **label and comment text**, never the IRI string; read
the comment to pick the right specificity when several match (see `resolve-class`):

```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
SELECT ?class ?label ?comment WHERE {
  ?class a owl:Class .
  OPTIONAL { ?class rdfs:label   ?label }
  OPTIONAL { ?class rdfs:comment ?comment }
  FILTER( CONTAINS(LCASE(STR(?label)),   "<TERM>")
       || CONTAINS(LCASE(STR(?comment)), "<TERM>") )
}
```

## 2. Read the precomputed amount in ONE hop

```sparql
PREFIX fq:      <https://www.purl.org/futuram/query#>
SELECT ?amount WHERE {
  <ClassIRI> fq:contains [ fq:constituent <ConstituentIRI> ; fq:amount ?amount ] .
}
```

The constituent's KIND comes from the ontology if you need to filter:
`?e rdfs:subClassOf* futuram:Element` (or `Material` / `Component`). The
unattributed remainder is a first-class `futuram:unknown*` constituent, so each
level's amounts sum to 1.0.

**`fq:contains` (what is INSIDE) vs `futuram:partOf` (a part SCOPED TO a whole) —
pick the right one, they answer different questions.**
- `fq:contains` reads the constituents OF a class you already have. `<X> fq:contains
  [ fq:constituent ?c ; fq:amount ?a ]` gives the materials/elements that make up X
  (and their fractions). Use it when X is the WHOLE you're asking about — a whole's
  own elements, a material's elements — and the answer is the list of constituents.
- `futuram:partOf` points a CONTEXTUAL scope node at the whole it sits in. The fq
  view precomputes, for a component inside a specific whole, a node that
  `futuram:partOf` that whole, is `rdf:type` the component class, and carries its
  OWN `fq:itemMass` + `fq:contains` for THAT context. Use it when the question is
  "the components OF whole W" or "how is element E distributed ACROSS W's
  components" — you want the per-component nodes, identified by
  `?node futuram:partOf <wholeClass>`, NOT the whole's own flat `fq:contains`
  list (that gives constituents, not components, and is the global average otherwise).
- Rule of thumb: asking what something is MADE OF → `fq:contains` on that thing.
  Asking to break a whole down BY ITS COMPONENTS (or to scope a component to one
  whole) → the component scope nodes via `futuram:partOf`. `futuram:partOf` is the
  `futuram:` namespace, not `fq:`; and `fq:sliceOf` is a DIFFERENT edge (a rollup
  axis pointing a slice at its base class) — it does NOT scope a part to a whole.
  See the `component-in-whole-fq` skill for the full scope-node query.
- TRIGGER PHRASES for the scope-node path: "the distribution of X across the
  components", "which component contributes most", "how is X split among the parts",
  "the components of whole W" — all of these break a whole down BY COMPONENT, so
  bind the per-component scope nodes by `?node futuram:partOf <wholeClass>`, NOT the
  whole's own `fq:contains` (which lists constituents and is the cross-whole
  average). The scope node carries its own `fq:itemMass`, so each component's absolute
  mass is `?frac * ?itemMass` on THAT node.
- LABEL each component row with the SCOPE NODE's own IRI — the `?node` bound by
  `futuram:partOf` (e.g. the `…_in_<whole>` node), NOT the abstract component class
  you typed it with. The abstract class is the global average; the scope node is the
  in-context value the question asked for, so its IRI is what identifies the row.
- When the question names a specific whole, do NOT use the component class IRI as
  the subject of `fq:itemMass` or `fq:contains` directly — that hits the global
  average, not the in-context value. Reach the data through
  `?node futuram:partOf <WholeYearIRI> ; rdf:type <ComponentClassIRI>` and read
  `?node`'s own `fq:itemMass`. This applies equally when computing a category total
  (e.g. sum of a specific element family in a component): bind the scope node first,
  then filter constituents inside it. The class IRI is only correct when the question
  genuinely asks for the cross-whole average.

**Ranging over SUBCLASSES — pick the right one of two shapes (each has its own skill).**
Whenever a question ranges over the subclasses of a class, discover them from the data's
`rdfs:subClassOf` edges — NEVER a hand-typed `VALUES` list (the data reliably has more
subclasses than you can name, so a typed list comes back short). There are TWO shapes;
they differ in WHICH role the subclass plays and direct-vs-transitive:
- **One number PER subclass (a "for EACH …" breakdown)** — the subclass is the SUBJECT
  (each is a whole with its own `fq:itemMass`+`fq:contains`); bind `?sub rdfs:subClassOf
  <ParentClassIRI>` (DIRECT), one row each. Get the `per-subclass-breakdown-fq` skill.
- **A TOTAL/LIST over a KIND inside one fixed whole** — the subclass is the CONSTITUENT;
  read the whole's `fq:contains` and keep `?key rdfs:subClassOf* <FamilyClassIRI>`
  (TRANSITIVE closure), summed or listed. Get the `total-over-kind-fq` skill.
Mixing them up is a top error (a `*` where you needed a direct hop over-pulls; a direct
hop where you needed `*` misses deep members). Fetch the matching skill for the exact
query + the pitfalls.

**The DIRECT subclasses of a level** (e.g. the top-level components OF a whole, not
their sub-parts): a class that is `rdfs:subClassOf <LevelRoot>` with NO intermediate
class between it and the root —
`?c rdfs:subClassOf <LevelRoot> . FILTER NOT EXISTS { ?c rdfs:subClassOf ?mid . ?mid rdfs:subClassOf <LevelRoot> . FILTER(?mid != <LevelRoot>) }`.
This picks the immediate subclasses and excludes their deeper sub-classes.

**The remainder is ALREADY in the `rdfs:subClassOf` closure — do not add a separate branch for it.**
`futuram:unknownElement rdfs:subClassOf* futuram:Element` is `true` (likewise
`unknownMaterial`/`unknownComponent` under their parents), so a single
`?e rdfs:subClassOf* futuram:Element` pattern already includes the remainder.
NEVER write a `UNION { VALUES ?e { futuram:unknownElement } }` (or a second
`subClassOf` branch) on top of it: `unknownElement` then matches BOTH branches,
the same `(constituent, amount)` row is produced twice, and a `SUM(...) GROUP BY`
with no `DISTINCT` double-counts it (you get exactly 2× the remainder). One
membership pattern only. If you ever must UNION overlapping patterns before an
aggregate, wrap the union in a `SELECT DISTINCT` subquery first.

## 3. Absolute mass, if asked

`fq:amount` is kg/kg. Multiply by the class's `fq:itemMass` (absolute kg of one
item) for absolute kilograms — but do the multiply **inside the SPARQL query**
(`SELECT (?frac * ?itemMass AS ?kg)`), never by hand. Trace constituents arrive in
scientific notation (e.g. `1.2854E-5`); hand-multiplying those by a ~2000 kg item
mass is where answers go wrong. See the `absolute-mass-fq` skill for the exact
pattern.

## 4. Answer

Report the value with its unit and end with the required `ANSWER:` line. This
endpoint already did the aggregation — one query should suffice.
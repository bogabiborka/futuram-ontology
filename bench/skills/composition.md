---
name: composition
description: How to answer a composition question on the RAW reified composition-statement endpoint (the /composition endpoint), where there is no precomputed aggregate of any kind and every amount lives in the statement tree. Use as the primary composition guide — resolving the class, traversing the statement structure to the parts, and computing the rest yourself.
metadata:
  backends: composition
---

# Skill — answering a composition question on the RAW composition endpoint
<!-- backends: composition -->

You are querying the reified `futuram:CompositionStatement` graph (the
`/composition` endpoint). There is **no precomputed aggregate of any kind** —
every amount lives in the statement tree and you compute the rest yourself.
Follow this procedure. In the queries, replace `<TERM>` with the
user's lowercased search term and `<ClassIRI>` with the class IRI you resolve.

**READ THIS FIRST — this endpoint loses runs to THRASHING, not to a hard concept.**
The failure here is almost never "couldn't find the data" — it is spending 20-40
discovery queries re-deriving the same hierarchy, choking on a cartesian-product
result, and never committing an answer. Avoid all three:

- **RESOLVE THE WHOLE ONCE, THEN COMMIT.** Resolve the asked class to ONE IRI (step 1),
  confirm it has instances with statements (step 2), and from then on STOP re-searching
  classes/labels/subClassOf — every later query is a DATA query against that resolved
  whole. If you have run more than a couple of discovery queries, you are thrashing:
  go to the statement query NOW.
- **SCOPE EVERY HOP — a cartesian product means you forgot a join, not that the data is
  messy.** A blown-up result (the same value repeated, an unexpected cross-product) is
  ALWAYS an under-constrained query: bind the top `?whole a <ClassIRI>` (never leave it
  free — an unbound whole sums the constituent over the ENTIRE graph), and make each
  deeper hop's whole be the PREVIOUS hop's part (`?mid` of hop 1 is the `?whole` of hop
  2). Do not "explore why it's messy" with more probes — re-write the ONE query with the
  missing join.
- **GET THE TIME SLICE FROM THE RESOLVED CLASS, NOT BY GUESSING INSTANCE NAMES.** If the
  question names a year, resolve the time-scoped subclass for that year FIRST (step 1)
  and bind instances with `?inst a <TimeScopedClassIRI>` — do NOT pattern-match instance
  local-names (e.g. `comp_…__V…_2010`) or hunt for a `period` predicate by trial. The
  year lives on the class; type the instance to the right class and the slice is handled.
- **ALWAYS END WITH AN ANSWER.** If you have the per-hop numbers, do the multiply/sum in
  ONE final query (or combine the few rows) and write the ANSWER line. A partial value
  from data you already have beats burning the budget and emitting nothing.

## 1. Resolve the thing the user described to the BEST-FITTING class IRI

The user's term names a class only sometimes; often it DESCRIBES one (by a part's
function, or a thing by the material it is made of) and you must find the class that
best fits — it may not contain the term verbatim. Treat this as a search-and-rank
step, not an exact lookup:

1. Cast a WIDE net: gather candidate classes by their **`rdfs:label` and
   `rdfs:comment`**, matching the term, its synonyms, and word stems. The class IRI
   is OPAQUE (a meaningless code) — the meaning lives in the label and comment, so
   search THOSE, NEVER the IRI string:
   ```sparql
   PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
   PREFIX owl:  <http://www.w3.org/2002/07/owl#>
   SELECT DISTINCT ?cls ?label ?comment WHERE {
     ?cls a owl:Class .
     OPTIONAL { ?cls rdfs:label   ?label }
     OPTIONAL { ?cls rdfs:comment ?comment }
     FILTER( CONTAINS(LCASE(STR(?label)),   "<TERM>")
          || CONTAINS(LCASE(STR(?comment)), "<TERM>") )   # the MEANING, never the IRI
   }
   ```
   If nothing hits, broaden: try a shorter stem, a synonym, or a more general
   word, and re-run the SAME label/comment query — never fall back to matching the
   IRI string. Inspecting `search_sparql_docs` / the base-class blocks in the VoID
   tells you which kinds (Product/Component/Material/Element) exist.
2. RANK the candidates and pick the best fit: prefer the closest meaning and the
   more specific class only if the user was specific. **If the question names a YEAR
   or PERIOD, resolve the TIME-BASED CLASS for that year and answer on it, NEVER the
   base** — the base is the all-years mean and is the WRONG number for a year
   question (find the base's time-scoped subclass whose label/comment names the asked
   year, and make THAT your class; do not answer on the base and filter a year value).
   Only when no year is named is the base correct. If several plausibly fit, briefly
   say which you chose and why. `<ClassIRI>` below is that chosen class.

**Do NOT filter the whole/part by a class-name string** (the whole and the
parts are instances with unrelated local names — reach the class via rdf:type).

## 2. Find the INSTANCES of that class (the step that's easy to miss)

The whole of a statement is the subject of `futuram:hasCompositionStatement`;
each part is the object of `futuram:refersTo` on a `futuram:PartRelation`. Both
are **instances**. Each instance is typed to its class AND up to its named base
(the type closure is materialised), so a plain `?inst a <ClassIRI>` binds them —
use the base class IRI to reach a whole kind at once:

```sparql
PREFIX futuram: <https://www.purl.org/futuram#>
SELECT DISTINCT ?inst WHERE {
  ?inst a <ClassIRI> .
  ?inst futuram:hasCompositionStatement ?stmt .
}
```

## 3. Read the amount, ALWAYS with its unit

The amount hangs off the `futuram:PartRelation` (one per qualified part), reached
via `?stmt futuram:hasPartRelation ?pr`:

```sparql
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX qudt: <http://qudt.org/schema/qudt/>
SELECT ?part ?v ?u WHERE {
  ?inst futuram:hasCompositionStatement ?stmt .
  ?stmt futuram:hasPartRelation ?pr .
  ?pr   futuram:refersTo ?part ;
        futuram:hasQuantity/futuram:hasBestValue [ qudt:numericValue ?v ; qudt:unit ?u ] .
}
```

Units are MIXED. Read each amount's `qudt:unit` and normalise every value to one
common base by that unit's own factor BEFORE any math — do not assume or hardcode
a single unit (see the `read-amount-and-units` skill).

## 4. Traverse and aggregate (the work you must do yourself here)

The constituent the user asked about is usually several hops below the whole:
`whole → component → material → element`. For each path from an instance down to
the target constituent class, **multiply the normalised fractions along the
path**; **sum** the contributions of parallel paths. If the class has several
instances, the class-level value is the **mean** over them (per the class's
`futuram:hasAggregationStrategy`).

### Use multiple queries IF AND ONLY IF one query cannot do it
Default to a SINGLE query. Decompose into several queries (and combine the
results yourself) ONLY when SPARQL 1.1 genuinely cannot express the answer — and
conversely, never compute by hand what one query could have returned.

**SPARQL 1.1 CAN** (so keep it in the query): arithmetic on bound values
(`?a * ?b`, `?a + ?b`) in BIND/SELECT; `SUM`/`AVG`/`COUNT` with `GROUP BY` (sum
parallel paths to one element, mean over a class's instances); a FIXED-LENGTH
join multiplying its hops' fractions (`?f1 * ?f2 * ?f3`); `rdfs:subClassOf*` and
property paths `p+`/`p*` to test that a path EXISTS.

**SPARQL 1.1 CANNOT** (these — and only these — justify combining queries):
multiply the per-edge fractions along a VARIABLE-length path (a `p+`/`p*` path
carries no per-edge value, so it can't build the running product down a tree of
unknown depth); recurse arithmetically to a fixed point (no `WITH RECURSIVE`). So
a constituent's share of a whole at irregular depth is NOT one query.

When that is the case: run one query PER hop depth (each a fixed-length join that
multiplies its own fractions and `SUM`s parallel paths via `GROUP BY`), then
combine the few aggregated rows yourself — sum per-depth contributions, mean
across instances. Push as much math into each query as possible.

## 5. Answer

Report the aggregated number with the unit you computed in (kg/kg unless asked
for absolute kg — then multiply by the whole's `futuram:itemMass`). End with the
required `ANSWER:` line. If you cannot complete the aggregation within the step
budget, report your best partial value and say so — do not invent a number.
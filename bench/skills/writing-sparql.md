---
name: writing-sparql
description: A generic SPARQL 1.1 language reference — the syntax, the feature set, and the mistakes that make a query error or return zero rows (typo'd IRIs, unbound variables, missing prefixes). Fetch this before writing your FIRST query on either endpoint. It describes the LANGUAGE only and names no dataset class, predicate, or modelling pattern — for those, read the endpoint VoID and the domain skills.
---

# Skill — writing CORRECT SPARQL 1.1 (the language reference)

This is a generic SPARQL 1.1 reference: the syntax, the feature set, and the
mistakes that make a query error or return zero rows. It says NOTHING about any
particular dataset — no predicates, no class shapes, no modelling patterns. For
what THIS dataset's classes and predicates are, read the endpoint's VoID
(`search_sparql_docs`) and the domain skills; for the exact prefixes, fetch the
`prefixes` skill. This skill is only about the LANGUAGE.

All examples below use throwaway, illustrative terms (`ex:s`, `ex:p`, `ex:Thing`,
`?x`, `?y`) under an invented namespace; they exist to show SYNTAX, not a schema.

```
PREFIX ex:   <https://example.org/ns#>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
```

## The five mistakes that error or return zero rows

1. **A typo'd namespace / IRI.** An IRI matches CHARACTER-FOR-CHARACTER — `http`
   vs `https`, a dropped letter, a `/` mistyped — and one wrong byte joins nothing.
   Copy IRIs; never retype them from memory.
2. **A made-up local name.** If a tool or an earlier query gave you an IRI, reuse
   THAT exact IRI. A name you spelled yourself from an English word is a guess.
3. **An expression variable you never bound.** `SUM(?x * ?y)` is empty/null if `?y`
   appears in no triple pattern. Every variable inside an arithmetic or aggregate
   expression must be bound by a triple in the WHERE.
4. **An undeclared prefix.** Every `pfx:local` needs a `PREFIX pfx: <…>` line.
5. **A full `<…>` IRI with the wrong scheme.** Prefer a prefixed name (`ex:Thing`)
   over a spelled-out `<…>` IRI so prefix tooling can normalise it.

## Writing an IRI — full `<…>` form vs prefixed name

A resource is named either by a FULL IRI in angle brackets, or by a prefixed name
that expands against a `PREFIX` declaration. The two are EQUIVALENT when the prefix
resolves to the same namespace:

```
<https://example.org/ns#Thing>        # full IRI — the complete absolute IRI in < >
ex:Thing                              # prefixed name — PREFIX ex: <https://example.org/ns#>
a                                     # keyword shorthand for rdf:type
```

- A full `<…>` IRI must be the WHOLE absolute IRI, with its scheme (`https://`),
  host, path and fragment, copied exactly — no spaces inside, always closed with `>`.
- A prefixed name is `prefix:local`; the part before `:` must have a matching
  `PREFIX` line, and `local` may not contain a space or a `/`.
- `<>` (empty) and a bare word are NOT valid IRIs.
- PREFER the prefixed name over a spelled-out full IRI: it is shorter, and the
  prefix machinery can normalise the namespace for you. Reserve full `<…>` IRIs for
  a one-off resource that has no prefix — and then copy it verbatim from a tool/VoID,
  never type it from memory.

## Query forms

```
SELECT ?x ?y WHERE { ?x ex:p ?y . }                       # basic graph pattern
ASK   WHERE { ?x ex:p ?y . }                              # boolean: does it exist
CONSTRUCT { ?x ex:q ?y } WHERE { ?x ex:p ?y }            # build a new graph
DESCRIBE ?x WHERE { ?x ex:p ?y }                         # CBD of the resource
```

A `WHERE` is a set of triple patterns. `subject predicate object .` Reuse a subject
with `;` and reuse subject+predicate with `,`:
```
?x ex:p ?y ;        # same subject ?x
   ex:q ?z .
?x ex:r ?a , ?b .   # ?x ex:r ?a . ?x ex:r ?b .
[ ex:p ?y ]         # a blank node: "some node whose ex:p is ?y"
```

## Projecting expressions — the alias goes INSIDE the parentheses

The single most common syntax error. Whenever you project a computed EXPRESSION,
wrap `(expr AS ?name)` in parentheses:
```
SELECT (?a * ?b AS ?prod)        WHERE { … }   # CORRECT
SELECT (SUM(?a) AS ?total)       WHERE { … }   # CORRECT
SELECT (SUM(?a)) AS ?total       WHERE { … }   # WRONG — AS outside the parens
SELECT  SUM(?a)  AS ?total       WHERE { … }   # WRONG — no parens at all
```

## Aggregates + GROUP BY

```
SELECT ?g (SUM(?v) AS ?s) WHERE { ?x ex:group ?g ; ex:val ?v } GROUP BY ?g
```
- Functions: `(SUM(?v) AS ?s)`, `(AVG(?v) AS ?a)`, `(COUNT(?v) AS ?n)`,
  `(COUNT(DISTINCT ?v) AS ?n)`, `(MIN(?v) AS ?lo)`, `(MAX(?v) AS ?hi)`,
  `(SAMPLE(?v) AS ?one)`, `(GROUP_CONCAT(?v; SEPARATOR=", ") AS ?list)`.
- Every NON-aggregated projected variable must appear in `GROUP BY`.
- An aggregate with no `GROUP BY` collapses the whole result to one row.
- Filter groups with `HAVING(<expr>)` — the aggregate equivalent of FILTER.

## Arithmetic & assignment

- Operators on bound values: `?a + ?b`, `?a - ?b`, `?a * ?b`, `?a / ?b`.
- `BIND(<expr> AS ?x)` names an intermediate inside the WHERE.
- `VALUES ?x { ex:a ex:b ex:c }` pins a fixed inline set (one var), or
  `VALUES (?x ?y) { (ex:a 1) (ex:b 2) }` for tuples.

## Optional, alternatives, negation

```
OPTIONAL { ?x ex:p ?y }                  # keep the row even if ?y is absent
{ ?x ex:p ?y } UNION { ?x ex:q ?y }      # either pattern
FILTER NOT EXISTS { ?x ex:p ?y }         # ?x has no ex:p
MINUS { ?x ex:p ?y }                     # subtract solutions that match
FILTER EXISTS { ?x ex:p ?y }             # ?x has some ex:p
```
Guard possibly-unbound vars with `BOUND(?y)` or `COALESCE(?y, 0)`.

## FILTER

`=  !=  <  <=  >  >=`, boolean `&&  ||  !`, and `?x IN (ex:a, ex:b)`,
`?x NOT IN (…)`. e.g. `FILTER(?n > 10 && ?n < 100)`.

## Property paths

`p/q` sequence, `p|q` alternative, `p+` one-or-more, `p*` zero-or-more,
`^p` inverse, `!p` negated, `(p)` grouping. e.g. `?x rdfs:subClassOf* ?root`
reaches a class and all ancestors; `?i rdf:type/rdfs:subClassOf* ?c` type-tests up
a tree. A path tests CONNECTIVITY; it carries no per-edge value.

## Solution modifiers

`DISTINCT`, `REDUCED`, `ORDER BY ?x` / `ORDER BY DESC(?x)`, `LIMIT n`, `OFFSET n`.

## Subqueries

```
SELECT ?x ?total WHERE {
  ?x ex:p ?y .
  { SELECT ?x (SUM(?v) AS ?total) WHERE { ?x ex:val ?v } GROUP BY ?x }
}
```
Use a subquery to aggregate first then join, and to DE-DUPLICATE rows before a
`SUM` (summing rows produced twice double-counts).

## Functions (selected)

- Strings: `CONCAT`, `SUBSTR`, `STRLEN`, `UCASE`, `LCASE`, `CONTAINS`,
  `STRSTARTS`, `STRENDS`, `STRBEFORE`, `STRAFTER`, `REPLACE(?s,"a","b")`,
  `REGEX(?s, "pat", "i")`.
- Terms: `STR(?x)`, `LANG(?x)`, `DATATYPE(?x)`, `isIRI(?x)`, `isLiteral(?x)`,
  `isBlank(?x)`, `BOUND(?x)`, `IF(c, a, b)`, `COALESCE(?a, ?b, 0)`.
- Numerics: `ABS`, `ROUND`, `CEIL`, `FLOOR`, casts `xsd:decimal(?s)`,
  `xsd:integer(?s)`, `xsd:double(?s)`.

## What SPARQL 1.1 CANNOT do

- It cannot multiply per-edge values ALONG a variable-length path (`p+`/`p*` carry
  no per-edge value, so no running product down a tree of unknown depth).
- It cannot recurse arithmetically to a fixed point (no `WITH RECURSIVE`).
- So a value that depends on an unbounded recursive computation is not one query:
  run one FIXED-LENGTH query per depth and combine the few results yourself.

## Discovering classes by text (label / comment), never by IRI string

A class IRI is opaque — its meaning is in `rdfs:label` (name) and `rdfs:comment`
(description). Discover by searching those TEXT fields, case-insensitively:
```
SELECT ?c ?label WHERE {
  ?c a owl:Class .
  OPTIONAL { ?c rdfs:label ?label }
  OPTIONAL { ?c rdfs:comment ?cmt }
  FILTER( CONTAINS(LCASE(STR(?label)), "term") ||
          CONTAINS(LCASE(STR(?cmt)),   "term") )
}
```
Make label/comment OPTIONAL (a mandatory one drops classes that lack it). Use a
substring scan ONCE to find candidates; then use the resolved IRI exactly.

## When a query returns 0 rows — relax ONE thing at a time

1. `SELECT * WHERE { <yourIRI> ?p ?o } LIMIT 20` — empty ⇒ the subject IRI is wrong
   (typo / scheme / made-up name). Re-fetch and copy it verbatim.
2. Drop the most specific FILTER / pattern — if rows appear, that constraint was
   wrong (wrong object IRI or literal).
3. `SELECT DISTINCT ?p WHERE { <yourIRI> ?p ?o }` — read the predicates the subject
   ACTUALLY has; use one of those, not an invented name.
4. Confirm every variable in an aggregate/arithmetic expression is bound.

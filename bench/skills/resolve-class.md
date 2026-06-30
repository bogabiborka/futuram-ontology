---
name: resolve-class
description: Resolve a plain-language term (a product, component, material, or element the user describes rather than names) to the best-fitting class IRI, by searching the rdfs:label and rdfs:comment (never the opaque IRI string) and ranking candidates. Use on either endpoint at the start of almost every question — including picking the right specificity (a narrow subclass vs a broad roll-up), the year/period-specific class, and committing to one class instead of re-searching.
---

# Skill — resolve a plain-language term to the BEST-FITTING class IRI

The user's words point at a CLASS (a product, component, material or element),
not an instance — but often they DESCRIBE the class rather than name it (a
function, a material made of something, a part by what it does). So this is a
search-and-rank step: gather candidates, then pick the best fit. Do not assume
the term appears verbatim.

In the queries below, replace `<TERM>` with the user's lowercased search term (or
a synonym / word stem of it) and `<ClassIRI>` with the class IRI you resolve.

## Two ways to find candidates — prefer the SEMANTIC tool, fall back to SPARQL
There are two discovery routes; use the first, drop to the second only when needed.
1. **`find_candidate_classes` (SEMANTIC / RAG search) — try this FIRST.** It ranks
   classes by the MEANING of their `rdfs:label`+`rdfs:comment` against your term
   using embeddings, so it finds the right class even when no word matches verbatim
   (a part named by its function, a thing named by its material, a paraphrase). Pass the `endpoint_url` you
   are querying and ask for MANY candidates in the ONE call (default 25) so a single
   search shows the whole neighbourhood — then read the ranked list, pick the IRI
   whose label/comment fits, and COMMIT. It returns each candidate's full IRI, label
   and comment; do not string-match the IRI yourself.
2. **SPARQL label/comment substring scan (below) — the fallback.** Use it when the
   semantic tool is unavailable, when you want an exhaustive case-insensitive match on
   a specific word/stem, or to confirm a candidate. Pull a generous set in one query
   (no tiny `LIMIT`).

Both search the label/comment MEANING, never the IRI string. Whichever you use,
resolve each term in ONE pass and commit — do not alternate between the two re-running
the same term.

### The candidate list is a SEED, not the SET — for a group, walk the hierarchy in SPARQL
`find_candidate_classes` returns a SIMILARITY-ranked handful — the nearest few
classes, never a complete set. When the question ranges over a GROUP (one number
"for EACH …", "the distribution of …", a TOTAL over a kind), the candidates it
surfaces are an INCOMPLETE slice: they are usually siblings under one
`rdfs:subClassOf` parent, and the search returns only some of them. Do NOT answer by
hand-listing those candidates in a `VALUES` block — that set is reliably short (you
get the few the search ranked highest and miss the rest).

Instead, use the candidates only to LOCATE the parent, then DISCOVER the full set
yourself in SPARQL: take one candidate, read its `rdfs:subClassOf` parent (the one
that is NOT a level root — not `futuram:Product`/`Component`/`Material`/`Element`),
and query that parent's children for the COMPLETE group, computing each one's value
in the same query:

```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?sub WHERE { ?sub rdfs:subClassOf <ParentClassIRI> }   # the whole set, from the data
```

The hierarchy is in the data — get it with SPARQL, never from the search list or a
typed list. The candidates reveal that the parent exists; the parent's subclasses are
the answer.

## Critical: the class IRI is OPAQUE — search the `rdfs:label` AND `rdfs:comment`
Treat every class IRI as a **random, meaningless identifier** (it may be an opaque
code or contraction that does NOT spell the concept — that is realistic and
intended). The human meaning of a class lives ONLY in its **`rdfs:label`** (its
name) and **`rdfs:comment`** (what it IS / how it was derived). Every served class
carries a label and most carry a comment. So discovery ALWAYS matches the **label
and the comment text**, case-insensitively — **NEVER the IRI string**. Filtering on
the class IRI is wrong: it assumes the IRI spells the concept, which it does not,
so it misses the class or matches the wrong one.

## Procedure
1. ONE discovery query — match the **label and the comment** (below), trying the
   term, its stem, synonyms, and each significant word. This is the only place a
   string filter belongs, and it filters label/comment text, never the IRI. Pull a
   GENEROUS set in this one query (no tiny `LIMIT` — ask for the whole neighbourhood,
   `LIMIT 100` or none): a wider list now is cheaper than re-searching the same term
   five times because the first list was truncated. If you call the
   `find_candidate_classes` tool instead, ask for many candidates in the SINGLE call.
2. If a search returns nothing, **broaden — do not give up**: shorter stem,
   synonym, a more general word, or each significant word separately, and re-run
   the SAME label/comment query. Do NOT fall back to matching the IRI string —
   that is never the fix.
3. Read each candidate's `rdfs:comment` and choose the closest meaning. The comment
   says whether a class is a broad DERIVED AGGREGATE (a roll-up over its subclasses)
   or a SPECIFIC, directly-composed class — a general term and a specific subclass can
   share a label but hold different values, so pick the SPECIFICITY the question
   pins. Say which you picked and why. If the question pins an ATTRIBUTE that implies
   a NARROWER subclass than the generic term (a qualifier on the kind of component or
   material — a defining property, technology, grade, or use), do NOT
   stop at the generic parent: that parent is the roll-up over ALL its subclasses and
   gives a diluted, WRONG number. Walk `rdfs:subClassOf*` DOWN from the generic class
   and read the children's labels/comments to find the subclass whose description
   matches the pinned attribute, and answer on THAT subclass. Only fall back to the
   generic parent when no child matches the qualifier.
   **Explicit classifiers in the question are HARD CONSTRAINTS — they beat semantic
   similarity.** If the question names a discrete code or letter (a category code,
   a technology type, a grade), the resolved class MUST match that token exactly in
   its `rdfs:label`. A candidate whose label matches a general description but carries
   a DIFFERENT code is the WRONG class — reject it even if the semantic search ranked
   it highly. Verify the label of the chosen candidate contains the exact token from
   the question before committing.
4. **COMMIT and STOP searching.** The moment ONE candidate's label/comment fits the
   term, that IRI is your answer for this term — adopt it and move straight to the
   DATA query. Do NOT re-run the candidate search, do NOT keep listing alternatives,
   do NOT "double-check" a class you already matched by searching for it again. Each
   term gets ONE resolution pass; resolving is NOT the answer — the data query is, and
   every extra discovery call spends budget you need to reach it. If you have resolved
   every term the question pins, stop resolving and write the data query NOW.

### YEAR / PERIOD — if the question names one, resolve the TIME-BASED CLASS, NEVER the base
If the question pins a year or period ("produced in 2025", "a 2020 model"),
you must RESOLVE and answer on the specific TIME-BASED CLASS for that year — a distinct
class, NOT the base. The base is the mean over ALL years and is a DIFFERENT, WRONG
number for a year question. Do NOT instead answer on the base and filter on a year
value — the time-correct amounts live on the time-based class itself, so that class
must be your subject. Method:
1. Resolve the base class by label/comment as usual.
2. From the base, take its time-scoped subclass for the asked year and make THAT the
   class you answer on. The time-based subclass is `rdfs:subClassOf` its base, and its
   `rdfs:label`/`rdfs:comment` name the year — match the
   asked year there to pick the right one. Answer every amount/uncertainty query
   against this resolved time-based class.
Only when the question names NO year is the base aggregate the right class to answer on.

```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
SELECT DISTINCT ?cls ?label ?comment WHERE {
  ?cls a owl:Class .
  OPTIONAL { ?cls rdfs:label   ?label }
  OPTIONAL { ?cls rdfs:comment ?comment }
  FILTER( CONTAINS(LCASE(STR(?label)),   "<TERM>") ||
          CONTAINS(LCASE(STR(?comment)), "<TERM>") )   # search the MEANING, never the IRI
}
```
Make label/comment OPTIONAL so a class missing one is not dropped. Once you have a
class IRI, use it directly as `<ClassIRI>`. If an earlier query already returned rows
for a whole (e.g. its constituents), that whole EXISTS — keep using that IRI; do not
re-litigate whether it is present, and do not re-run a substring scan to "re-find" a
class you already resolved.

## A real-world proper name is NEVER a class — translate it to its CATEGORY first
The classes are CATEGORIES (kinds / types), never individual brand or model names.
If the question names a specific real-world thing by its proper name or brand, that
exact name will NOT be in the data — searching for it returns nothing, and that is
EXPECTED. Restate the named thing by the categories it belongs to (read the class
labels/comments to learn WHICH dimensions the data classifies by), then search the
labels/comments for THOSE category words. If you only matched SOME of the pinned
attributes you have the wrong (too-broad) class — narrow to the class matching EVERY
attribute the question pins.

Tips (both endpoints):
- Match case-insensitively and by substring, on the LABEL and COMMENT only.
- If the question names a YEAR/PERIOD, answer on that year's SLICE and NEVER on the
  base — the base is the all-years mean and is the WRONG number for a year question.
  Only when no year is named is the base aggregate correct.
- After you have the class, get its instances with `find-instances-of-class`.
  Every individual is typed to its class AND up to its named base, so a plain
  `?inst a <ClassIRI>` (or `?inst a <BaseClassIRI>`) binds them — no property
  path needed.

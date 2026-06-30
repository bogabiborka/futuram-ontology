---
name: per-subclass-breakdown-fq
description: Answer a "for EACH …" question on the fq endpoint — one number PER direct subclass of a class (a breakdown over its immediate children). Each subclass is itself a WHOLE with its own fq:itemMass and fq:contains, so one row per subclass. Use when the question asks for a value per member of a set you must DISCOVER (not hand-list) — e.g. "for each X", "the breakdown by X", "per X". Bind the subjects with a single direct rdfs:subClassOf to the parent.
metadata:
  backends: fq
---

# Skill — the per-subclass BREAKDOWN (one row per direct subclass) — fq endpoint
<!-- backends: fq -->

The question asks for ONE number PER subclass of a class — a breakdown over its
immediate children, each child reported on its own row. Those children are NOT
something you enumerate from the question text or guess from an IRI pattern: they are
the DIRECT `rdfs:subClassOf` children of one parent class, and the data defines the
complete set. Hand-typing them in a `VALUES` block is the #1 cause of a wrong answer
here — the data almost always has MORE children than you can name (an extra family,
an averaged "unspecified" member), so a typed list comes back SHORT.

## The shape
Each subclass is itself a WHOLE: it carries its OWN `fq:itemMass` and its OWN
`fq:contains` amounts. So you bind the subclass as the SUBJECT and read its value,
one row each. Resolve the parent class ONCE (see `resolve-class`), then:

```sparql
PREFIX fq:      <https://www.purl.org/futuram/query#>
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?sub (?m * ?f AS ?kg) WHERE {
  ?sub rdfs:subClassOf <ParentClassIRI> ;          # ALL the direct subclasses, discovered
       fq:itemMass ?m ;
       fq:contains [ fq:constituent <ConstituentIRI> ; fq:amount ?f ] .
}
ORDER BY DESC(?kg)
```

- **Direct `rdfs:subClassOf`, NOT `rdfs:subClassOf*`.** You want the immediate children
  of the parent, one row each; the transitive `*` would also pull each child's own
  deeper subclasses and produce extra/duplicate rows.
- **Resolve the RIGHT parent.** The subjects all hang off ONE parent class — resolve it
  by `rdfs:label`/`rdfs:comment` (`resolve-class`), do not guess. If your result has
  FEWER rows than the question implies (e.g. you expected every member of a family but
  got only part), you resolved a too-narrow parent — go UP one level and re-run. The
  semantic class search returns only a similarity SEED, never the full set; the parent's
  `rdfs:subClassOf` children ARE the full set.
- **Label each row by the subclass IRI** (`?sub`) — the number describes that subclass,
  so its IRI is the label.
- Multiply `fq:itemMass * fq:amount` for absolute kg INSIDE the query (see
  `absolute-mass-fq`); drop the `* ?m` if the question wants the kg/kg fraction.

This is the SIBLING of `total-over-kind-fq`: there the subclass is the CONSTITUENT and
you fold the whole closure into one total (`subClassOf*`); here the subclass is the
SUBJECT and you report each direct child on its own row (`subClassOf`).

## "Rank … in decreasing order" — the order is PART of the answer
When the question says "rank", "in decreasing/descending order", "ranked", or "from
highest to lowest", the ORDER of the rows is itself scored — not just the per-row
values. Two rules:

1. **Let SPARQL do the ordering** — `ORDER BY DESC(?kg)` (the query already above).
   Never re-sort by hand; emit the rows in exactly the order the query returned them.
2. **Hand in the answer IN THAT ORDER.** The `values` and `labels` arrays of your
   ANSWER must be listed top-to-bottom in the same descending order, each label the
   subclass IRI, each value its kg (and the `± uncertainty` per row if asked):

   ```
   ANSWER: {"values": [<highest>, <next>, …, <lowest>],
            "uncertainties": [<unc of highest>, …, <unc of lowest>],
            "labels": ["<SubclassIRI-highest>", …, "<SubclassIRI-lowest>"],
            "unit": "kg"}
   ```

   A correct set of values in the WRONG order is a wrong ranking. Report EVERY
   subclass the query returned — do not truncate to a "top N"; "rank ALL" means all of
   them, smallest included.

## The value IS in the data — don't stop at itemMass
Every subclass here is a WHOLE that carries BOTH its `fq:itemMass` AND its
`fq:contains [ fq:constituent <ConstituentIRI> ; fq:amount ?f ]` (and
`fq:relativeUncertainty` when ± is asked) on the SAME node. So the per-subclass kg is
always reachable — `?m * ?f`. If you have only the itemMass and slice parent but no
amount, you simply have not bound `fq:contains` yet: ADD it and re-run. Never hand in
a labels-only / empty-`values` answer as a "structural proxy" or "membership set" —
that scores ZERO. An empty result means the BINDING was wrong (wrong parent,
constituent, or slice), not that the number is missing; fix the pattern and query
again. The answer can be found in the dataset — keep going until you have the numbers.

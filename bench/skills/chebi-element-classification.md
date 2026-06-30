---
name: chebi-element-classification
description: Classify elements into chemistry categories (metals, non-metals, rare-earth, etc.) by following the bridge from the domain element classes into the ChEBI ontology and walking ChEBI's subclass tree, since the domain element classes do not carry the category themselves. Use whenever a question asks for a CATEGORY of element ("which are metals", "the rare-earth ones", "sum of rare-earth elements in a component") rather than a named element — including when the category total must be scoped to a specific component inside a specific whole (scope-node path via futuram:partOf).
---

# Skill — classify chemical elements with ChEBI (metals, etc.)

When a question asks for a CATEGORY of element ("which are metals", "the rare-earth
ones", "non-metals"), the FutuRaM element classes do not carry that category
themselves — they are bridged into the **ChEBI** chemistry ontology, whose
taxonomy supplies the categories. Use the bridge + ChEBI's subclass tree.

## The bridge
Each FutuRaM element class is `rdfs:subClassOf` its ChEBI counterpart (an
`obo:CHEBI_*` class). ChEBI then groups those elements under category classes
(e.g. a "metal atom" class, "rare earth metal atom", "nonmetal atom", …). So an
element's category is reachable by:

```
futuram:<Element>  rdfs:subClassOf  obo:CHEBI_<element>  rdfs:subClassOf*  obo:CHEBI_<category>
```

## Method
1. Find the ChEBI CATEGORY class for the asked category by its label — do NOT
   guess the numeric IRI. Search ChEBI classes whose label matches the category
   word (and synonyms/stem):
   ```sparql
   PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
   SELECT ?cat ?label WHERE {
     ?cat rdfs:label ?label .
     FILTER(CONTAINS(STR(?cat), "CHEBI"))
     FILTER(CONTAINS(LCASE(STR(?label)), "<CATEGORY>"))   # e.g. the asked category
   }
   ```
   Pick the class whose label best fits (e.g. the plain "<CATEGORY> atom" class,
   not a compound/ion variant). When several classes match, prefer the one labelled
   exactly "<CATEGORY> atom" — ChEBI has near-synonyms that differ by one numeric
   suffix; the right one is the plain atomic group, not a salt, ion, or metal-alloy
   subtype. Verify by checking the label of the class you intend to use before
   filtering with it.
2. Select the FutuRaM elements that fall under it, via the bridge + subclass tree.
   Use `rdfs:subClassOf*` (TRANSITIVE — the star is mandatory) on BOTH legs; an
   element sits several ChEBI levels below a broad category, so a one-hop
   `subClassOf` returns nothing:
   ```sparql
   PREFIX futuram: <https://www.purl.org/futuram#>
   PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
   SELECT DISTINCT ?el WHERE {
     ?el rdfs:subClassOf* <https://www.purl.org/futuram#Element> .
     ?el rdfs:subClassOf* <CATEGORYClassIRI> .       # the ChEBI category resolved in step 1
   }
   ```
3. To total the constituents of a COMPONENT SCOPED TO A SPECIFIC WHOLE (fq
   endpoint), combine the scope-node pattern from `component-in-whole-fq` with the
   ChEBI membership filter. Scope the component to the whole via
   `futuram:partOf <WholeYearClassIRI>`, filter constituents by
   `?c rdfs:subClassOf* <CATEGORYClassIRI>`, and SUM in one query:
   ```sparql
   PREFIX fq:      <https://www.purl.org/futuram/query#>
   PREFIX futuram: <https://www.purl.org/futuram#>
   PREFIX rdf:     <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
   PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
   PREFIX afn:     <http://jena.apache.org/ARQ/function#>
   SELECT (SUM(?kg) AS ?total) (afn:sqrt(SUM(?sig*?sig)) AS ?unc) WHERE {
     SELECT DISTINCT ?c (?m*?f AS ?kg) (?ru*?m*?f AS ?sig) WHERE {
       ?node futuram:partOf <WholeYearClassIRI> ;
             rdf:type <ComponentClassIRI> ;
             fq:itemMass ?m ;
             fq:contains [ fq:constituent ?c ;
                           fq:amount ?f ; fq:relativeUncertainty ?ru ] .
       ?c rdfs:subClassOf* <CATEGORYClassIRI> .
     }
   }
   ```
   The `SELECT DISTINCT` inner query deduplicates before the outer `SUM` aggregates.

   Resolve the WHOLE (the vehicle year class, e.g. `futuram:V…_Y2025`) and the
   COMPONENT class separately, then plug both into the template above. See the
   `component-in-whole-fq` skill for the scope-node rule (use `?node`, not the
   component class IRI, as the query subject and label).

## Notes
- The category lives in ChEBI, the amounts live in this endpoint's data — you
  always JOIN the two through the element CLASS (`?el`).
- `rdfs:subClassOf*` (transitive) is ESSENTIAL on every leg — a direct one-hop
  `subClassOf` will miss almost everything. This is the #1 cause of an empty
  result on category questions.
- Do NOT require `?el rdfs:label ?l` — some element classes may carry no label, so
  a MANDATORY label triple empties the result; make label/comment OPTIONAL. Identify
  a class by its **full IRI** (and its `rdfs:label`/`rdfs:comment` when present),
  never by string-matching the IRI.
- If you already got constituent rows for the whole in an earlier query, the whole
  EXISTS — do not conclude it is missing just because a later, more-constrained
  query came back empty. Relax the added constraint instead.
- If a category word matches several ChEBI classes, inspect their labels and pick
  the one that means the whole group; say which you chose.

---
name: find-instances-of-class
description: Find the individual instances of a class on the composition endpoint, where the statement wholes and parts are instances with arbitrary local names you cannot match by the class name as a string. Use whenever you have a class IRI and need its instances — bind them by the rdf:type + subclass path, not by name.
metadata:
  backends: composition
---

# Skill — find the INSTANCES of a class (composition endpoint)
<!-- backends: composition -->

On the `composition` endpoint, a statement's whole (subject of
`futuram:hasCompositionStatement`) and its parts (object of `futuram:refersTo` on
each `futuram:PartRelation`) are **instances**, not classes. The instances have
arbitrary local names — you cannot find them by matching the class name as a
string. Use the type + subclass path instead.

An instance is typed (`rdf:type`) to its own class AND to every ancestor up to
its named base class (the type closure is materialised). So a plain `?inst a
<ClassIRI>` binds the instances of that class — and `?inst a <BaseClassIRI>`
binds every instance of a whole kind. No property path is needed (replace
`<ClassIRI>` with the class you resolved):

```sparql
PREFIX futuram: <https://www.purl.org/futuram#>
SELECT DISTINCT ?inst WHERE {
  ?inst a <ClassIRI> .                                  # the resolved class IRI / base
  ?inst futuram:hasCompositionStatement ?stmt .         # keep only ones that are wholes
}
```

- `?inst a <ClassIRI>` reaches instances of the class; use the base class IRI to
  reach instances of every sub-class of a kind in one go.
- Drop the second line if you also want instances that only appear as parts.
- DO NOT `FILTER(CONTAINS(?inst, "<TERM>"))` — instance IRIs do not contain the
  class name.

A class-level number is an AGGREGATE over these instances (see
`aggregate-multihop`).
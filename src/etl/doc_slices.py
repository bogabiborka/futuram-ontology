# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml"]
# ///
"""etl.doc_slices — DOC-level (YAML side) time-slice authoring. LEAF slices (V…_Y2026)
are minted by the transformer; ANCESTOR slices are DERIVED here by derive_for_doc
(RDF side: builder.slicer.YearSlicer), strategy=equal-subclass-mean WITHIN the slice.
"""
from __future__ import annotations

import functools

LEVEL_ROOTS = {"Product", "Component", "Material", "Element"}
SLICE_STRATEGY = "equal-subclass-mean"


def slice_name(base, entry):
    """The naming convention for a time slice of `base` (labels only; the
    class_time registry / RDF annotations stay authoritative). Identical to
    builder.slicer.slice_name — the two sides agree on names by construction."""
    if "year" in entry:
        return f"{base}_Y{entry['year']}"
    return f"{base}_Y{entry['start']}_{entry['end']}"


def _scope_key(entry):
    return (entry.get("year"), entry.get("start"), entry.get("end"))


def _scope_fields(entry):
    return ({"year": entry["year"]} if "year" in entry
            else {"start": entry["start"], "end": entry["end"]})


def _slice_pairs(entry):
    """The (parent, axis) slice edges of a doc-form class_time entry. Accepts the
    authored 'slices' list (each item a {parent, axis} mapping, a (parent, axis)
    pair, or a bare-string parent that defaults to the year axis)."""
    out = []
    for item in entry.get("slices", ()):
        if isinstance(item, str):
            out.append((item, "year-slice-mean"))
        elif isinstance(item, (list, tuple)):
            out.append((item[0], item[1] if len(item) > 1 else "year-slice-mean"))
        elif isinstance(item, dict):
            out.append((item["parent"], item.get("axis", "year-slice-mean")))
    return out


def _year_base(entry):
    """The YEAR base of a doc-form entry: the parent of its year-slice-mean edge
    (the timeless taxonomy class the year dimension collapses to), or None."""
    for parent, axis in _slice_pairs(entry):
        if axis == "year-slice-mean":
            return parent
    return None


def derive_for_doc(doc, extra_superclasses=None):
    """Mutate a scenario doc: mint the ancestor slices for every year-slice
    class_time entry. Taxonomy parents come from the frozen futuram-hierarchy + the
    doc's subclass_of + `extra_superclasses`. Idempotent; returns the doc."""
    sup = _frozen_superclasses()

    sco = {sub: ([sups] if isinstance(sups, str) else list(sups))
           for sub, sups in (doc.get("subclass_of") or {}).items()}
    ct = doc.setdefault("class_time", {})

    def parents_of(cls):
        out = set(sup.get(cls, ()))
        out |= set(sco.get(cls, ()))
        if extra_superclasses:
            out |= set(extra_superclasses.get(cls, ()))
        return {p for p in out if p not in LEVEL_ROOTS}

    for slc, entry in sorted(ct.items()):
        base = _year_base(entry)
        if not base:
            continue
        scope = _scope_fields(entry)
        stack, seen = [base], set()
        while stack:
            cls = stack.pop()
            if cls in seen:
                continue
            seen.add(cls)
            for parent in sorted(parents_of(cls)):
                p_slice = slice_name(parent, entry)
                child_slice = slc if cls == base else slice_name(cls, entry)
                links = sco.setdefault(child_slice, [])
                if p_slice not in links:
                    links.append(p_slice)
                p_links = sco.setdefault(p_slice, [])
                if parent not in p_links:
                    p_links.append(parent)
                existing = ct.get(p_slice)
                if existing is not None and _scope_key(existing) != _scope_key(entry):
                    raise ValueError(
                        f"derive_for_doc: slice name collision {p_slice}: "
                        f"{existing} vs {entry}")
                ct.setdefault(p_slice, {
                    **scope,
                    "slices": [{"parent": parent, "axis": "year-slice-mean"}],
                    "strategy": SLICE_STRATEGY})
                stack.append(parent)

    doc["subclass_of"] = sco
    return doc


@functools.lru_cache(maxsize=1)
def _frozen_superclasses():
    """class local-name -> set of direct futuram superclass local-names, from the
    frozen futuram-hierarchy.ttl. Read straight from the file, parsed once."""
    from rdflib import Graph, RDFS
    from common import pipeline
    from common.vocab import FUT
    g = Graph()
    g.parse(str(pipeline.HIERARCHY), format="turtle")
    out = {}
    fut = str(FUT)
    for s, o in g.subject_objects(RDFS.subClassOf):
        if str(s).startswith(fut) and str(o).startswith(fut):
            out.setdefault(str(s).split("#")[-1], set()).add(str(o).split("#")[-1])
    return out

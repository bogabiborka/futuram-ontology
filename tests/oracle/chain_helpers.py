"""oracle.chain_helpers — pure chain/hierarchy queries over the oracle's own
state, with NO dependency on the builder / fq: projection. Live in oracle so
both the MC cache and the builder's resolver use them without a backwards edge.
"""
from __future__ import annotations


def descendant_leaf_classes(superclasses, cls_name):
    """Every leaf class below `cls_name` in the superclass DAG (no further
    subclasses, excluding `cls_name`). `superclasses` is the chain's own
    {class -> direct superclasses} map, not a process global."""
    seen, stack, leaves = set(), [cls_name], set()
    while stack:
        cur = stack.pop()
        children = [c for c, sups in superclasses.items() if cur in sups]
        if not children and cur != cls_name:
            leaves.add(cur)
        for c in children:
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return leaves


def stmt_iri_for(sc, s):
    """The content-hash IRI of statement `s` within chain `sc` (the same IRI the
    serializer mints, used as fq:derivedFromStatement)."""
    from .fastchain.serialize import _stmt_iri
    prov = sc.provenance or {}
    return _stmt_iri(s, f"{prov.get('source', '')}|{sc.id or ''}")

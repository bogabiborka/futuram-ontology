"""resolver.dedup — the generic DEDUP operation: dedup(g) collapses a plugin's own
duplicate unknown<level> remainder rows (keeping the max-value one) to EXACTLY ONE per
(subject, whole, element). Pass seen=<upstream> to drop rows a dependency already has.
"""
from __future__ import annotations

from .vocab import FQ


def _is_unknown(el):
    return el is not None and str(el).split("#")[-1].startswith("unknown")


def dedup(g, *, seen=None):
    seen_keys = set()
    if seen is not None:
        for subj in seen.subjects(FQ.contains, None):
            for a in seen.objects(subj, FQ.contains):
                el = seen.value(a, FQ.constituent)
                if _is_unknown(el):
                    seen_keys.add((subj, seen.value(a, FQ.whole), el))

    groups = {}
    for subj in g.subjects(FQ.contains, None):
        for a in g.objects(subj, FQ.contains):
            el = g.value(a, FQ.constituent)
            if _is_unknown(el):
                groups.setdefault((subj, g.value(a, FQ.whole), el), []).append(a)

    def drop(node, subj):
        for p, o in list(g.predicate_objects(node)):
            g.remove((node, p, o))
        g.remove((subj, FQ.contains, node))

    for (subj, whole, el), nodes in groups.items():
        if (subj, whole, el) in seen_keys:          # a dep already emitted it
            for n in nodes:
                drop(n, subj)
            continue
        if len(nodes) < 2:
            continue
        vmax = max(float(g.value(n, FQ.amount)) for n in nodes)
        keeper = next(n for n in nodes if float(g.value(n, FQ.amount)) == vmax)
        for n in nodes:
            if n is not keeper:
                drop(n, subj)
    return g

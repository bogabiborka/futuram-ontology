# (fastchain package — split from the single-file implementation; the
#  arithmetic is verbatim, only the module layout changed.)
"""The futuram class hierarchy: STATIC taxonomy map (futuram-hierarchy.ttl, loaded
ONCE) + ancestors_of. A scenario's subclass_of edges live PER-CHAIN (not a global)
and ancestors_of takes that map as a parameter — scenarios never contaminate."""
import pathlib
from collections import defaultdict

# Class hierarchy (futuram local-name -> [direct superclass local-names]) from
# futuram-hierarchy.ttl, so aggregation rolls an instance up to EVERY ancestor
# of its declared class. Extra superclasses outside the four level-roots kept.

# repo root = nearest ancestor with pyproject.toml (move-proof; never count levels)
def _repo_root():
    p = pathlib.Path(__file__).resolve()
    for cand in (p, *p.parents):
        if (cand / "pyproject.toml").exists():
            return cand
    return p.parent.parent.parent.parent
_HIER_PATH = _repo_root() / "ontology" / "tbox" / "futuram-hierarchy.ttl"
_LEVEL_ROOTS = {"Product", "Component", "Material", "Element"}


def _load_superclasses():
    """{class -> set(direct superclass localnames)} from the STATIC hierarchy TTL
    (TAXONOMY only; empty if absent). Also fills _HIER_STRATEGIES (enriched ABox's
    declared strategies) in the same parse. Scenario edges are layered PER-CHAIN."""
    from rdflib import Graph as _G, RDFS as _RDFS, URIRef as _U
    sup = defaultdict(set)
    if not _HIER_PATH.exists():
        return sup
    g = _G().parse(str(_HIER_PATH))
    fut = "https://www.purl.org/futuram#"
    for s, _, o in g.triples((None, _RDFS.subClassOf, None)):
        if str(s).startswith(fut) and str(o).startswith(fut):
            sup[str(s).split("#")[-1]].add(str(o).split("#")[-1])
    # declared aggregation strategies (enriched by enrich_hierarchy_abox.py):
    # every timeless P/C BASE carries year-slice-mean — the oracle only emits a
    # base aggregate under that declared strategy.
    from .vocab import STRATEGY_TOKEN
    for s, o in g.subject_objects(_U(fut + "hasAggregationStrategy")):
        token = STRATEGY_TOKEN.get(str(o).split("#")[-1])
        if token and str(s).startswith(fut):
            _HIER_STRATEGIES[str(s).split("#")[-1]] = token
    return sup


# class-localname -> declared strategy token, from the enriched hierarchy ABox
# (filled by _load_superclasses, which already parses the TTL). Static taxonomy
# data, never scenario-specific — so a process global is correct here.
_HIER_STRATEGIES: dict = {}

# The STATIC taxonomy map, loaded ONCE and treated as IMMUTABLE. Per-chain code
# builds its own map by copying this and layering the chain's subclass_of edges
# (see SupplyChain.superclasses) — this dict is never mutated after load.
_STATIC_SUPERCLASSES = _load_superclasses()


def chain_superclasses(subclass_of):
    """A fresh {class -> set(direct superclasses)} map = STATIC taxonomy plus a
    chain's own `subclass_of` edges, passed to ancestors_of WITHOUT mutating
    shared state. (Mirrors builder.index._ClassHierarchy.__init__.)"""
    sup = defaultdict(set)
    for c, sups in _STATIC_SUPERCLASSES.items():
        sup[c] |= sups
    for sub, sups in (subclass_of or {}).items():
        for s_ in (sups if isinstance(sups, (list, tuple, set)) else [sups]):
            sup[sub].add(s_)
    return sup


def ancestors_of(superclasses, cls_name, include_self=True):
    """All transitive superclass local-names of `cls_name`, minus the four
    level-roots (too generic); includes the class itself by default.
    `superclasses` is the chain's own map; no process global is read."""
    out = set()
    stack = [cls_name]
    seen = set()
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        if c != cls_name and c in _LEVEL_ROOTS:
            continue
        if include_self or c != cls_name:
            if c not in _LEVEL_ROOTS:
                out.add(c)
        for sup in superclasses.get(c, ()):
            stack.append(sup)
    return out

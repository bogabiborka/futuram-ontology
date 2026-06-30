"""Content-hash-keyed Monte-Carlo cache for class aggregates (MC resampled over a class's leaves; ~21h over the whole store, so ON DEMAND + cached). THE KEY IS THE STATEMENTS, NOT THE CLASS: sha1(sorted relevant stmt IRIs + samples + seed + pct), depending EXACTLY on the statements under its descendant leaves — a relevant change misses, others hit. Content hash IS the invalidation.
"""
import hashlib
import json
import os
import pathlib

from .chain_helpers import descendant_leaf_classes, stmt_iri_for
from .supplychain import FUT


def relevant_stmt_iris(chain, class_name):
    """The content-hashed statement IRIs feeding `class_name`'s MC: the statements
    under its descendant leaf classes (its own, for a leaf), identical to the
    resolver's fq:derivedFromStatement; via chain_helpers (no builder dependency)."""
    leaves = descendant_leaf_classes(chain.superclasses, class_name)
    targets = {class_name} | leaves          # leaf scope -> its own statements
    leaf_iris = {FUT[c] for c in targets}
    out = set()
    for s in chain.stmts:
        whole_cls = chain.nodes[s.whole].cls if s.whole in chain.nodes else None
        if whole_cls is not None and FUT[whole_cls] in leaf_iris:
            out.add(str(stmt_iri_for(chain, s)))
    return frozenset(out)


def mc_key(chain, class_name, *, samples, seed, percentiles):
    """The cache key: a hash of the relevant statement set + the MC parameters.
    Same statements + same params -> same key -> hit; any relevant statement
    added/changed/removed -> different set -> different key -> miss."""
    iris = relevant_stmt_iris(chain, class_name)
    payload = "\n".join(sorted(iris))
    payload += f"\n#samples={int(samples)}#seed={int(seed)}"
    payload += f"#pct={float(percentiles[0])},{float(percentiles[1])}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _load(cache_path):
    p = pathlib.Path(cache_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _store(cache_path, data):
    p = pathlib.Path(cache_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, p)                        # atomic on POSIX


def mc_for_class(chain, class_name, *, samples=200, seed=42,
                 percentiles=(5, 95), cache_path="mc_cache.json"):
    """On-demand MC for ONE class, cached by its relevant statement set. Returns
    {element_class: {best, lo, hi}}. Hit -> stored result; miss -> aggregate_mc
    SCOPED to this class's subtree, stored under the content-hash key (self-invalidating)."""
    key = mc_key(chain, class_name, samples=samples, seed=seed,
                 percentiles=percentiles)
    cache = _load(cache_path)
    entry = cache.get(key)
    if entry is not None:
        entry["_cache"] = "hit"
        return entry

    full = chain.aggregate_mc(samples=samples, seed=seed,
                              percentiles=tuple(percentiles),
                              scope_class=class_name)
    result = full.get(class_name, {})
    record = dict(result)
    record["_meta"] = {
        "class": class_name,
        "samples": int(samples),
        "seed": int(seed),
        "percentiles": [percentiles[0], percentiles[1]],
        "n_stmts": len(relevant_stmt_iris(chain, class_name)),
    }
    cache[key] = record
    _store(cache_path, cache)
    out = dict(record)
    out["_cache"] = "miss"
    return out

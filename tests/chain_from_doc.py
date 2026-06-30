# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml"]
# ///
"""chain_from_doc — build an oracle SupplyChain from an in-memory scenario doc.

TEST-SUPPORT ONLY (golden/parity tests): lives under tests/ so no src/ library
imports the oracle (the real ETL path needs no SupplyChain).
"""
from collections import defaultdict


def to_chain(path, sid=None, sheet=None, years=None, products=None,
             canonicalize=True, label=None):
    """Transform a dataset (CSV/Excel) straight into an oracle SupplyChain — no
    YAML file: transform() -> doc -> optional canonicalisation -> SupplyChain.
    TEST-SUPPORT (the real ETL path emits RDF via csv_to_rdf.to_graph)."""
    from etl import csv_to_rdf as X
    from etl import chain_loader
    doc = X.transform(path, sid=sid, sheet=sheet, years=years, products=products)
    if canonicalize:
        doc, _changes = chain_loader.canonicalize_doc(doc)
    return chain_from_doc(doc, label=label)


# the statement fields the loader handles explicitly; the rest are dist params.
_CORE = {"whole", "part", "best", "lo", "hi", "unit", "dist", "quality"}
_REQUIRED = ("best", "unit", "dist")


def _slice_name(base, scope):
    """Doc-side time-slice class name (mirrors etl.doc_slices.slice_name /
    builder.slicer.slice_name — the three sides agree by construction)."""
    if "year" in scope:
        return f"{base}_Y{scope['year']}"
    return f"{base}_Y{scope['start']}_{scope['end']}"


def _reslice_from_node_time(doc):
    """BRIDGE-ONLY: rebuild the frozen oracle's old leaf-slice/class_time shape from
    the live ETL's `node_time` (retype instances onto leaf slices, derive ancestors).
    Oracle untouched; no-op when the doc has no `node_time`."""
    node_time = doc.get("node_time")
    if not node_time:
        return doc
    from etl.doc_slices import derive_for_doc
    nodes = doc["nodes"]
    class_time = dict(doc.get("class_time") or {})
    subclass_of = {k: (list(v) if isinstance(v, list) else [v])
                   for k, v in (doc.get("subclass_of") or {}).items()}
    for name, scope in node_time.items():
        spec = nodes.get(name)
        if spec is None:
            continue
        base = spec["class"]
        leaf = _slice_name(base, scope)
        spec["class"] = leaf                       # retype the instance node
        class_time.setdefault(leaf, {
            **scope,
            "slices": [{"parent": base, "axis": "year-slice-mean"}]})
        links = subclass_of.setdefault(leaf, [])
        if base not in links:
            links.append(base)
    doc["class_time"] = class_time
    doc["subclass_of"] = subclass_of
    doc.pop("node_time", None)                      # consumed into the slice model
    derive_for_doc(doc)                             # ancestor slices (doc-side)
    return doc


def chain_from_doc(doc, label=None):
    """Build a SupplyChain from a scenario doc dict (the transformer's output or a
    parsed YAML). Mirrors SupplyChain.from_yaml's validation, in memory."""
    from oracle.supplychain import SupplyChain, UNIT_BY_NAME
    doc = _reslice_from_node_time(doc)   # node_time (new ETL) -> class_time (oracle)
    sc = SupplyChain(label=label or doc.get("title", ""), id=doc.get("id", ""))

    prov = doc.get("provenance")
    if not prov:
        raise ValueError("doc missing required 'provenance' block")
    for k in ("source", "agent", "production", "validFrom"):
        if k not in prov:
            raise ValueError(f"provenance missing required '{k}'")
    sc.provenance = dict(prov)

    sc.subclass_of = {}
    for sub, sup in (doc.get("subclass_of") or {}).items():
        sups = sup if isinstance(sup, list) else [sup]
        sc.subclass_of[sub] = list(sups)
    # the chain's `superclasses` property derives the ancestor map from
    # sc.subclass_of (static taxonomy + these edges) — no global mutation.

    # the time registry — same validation as SupplyChain.from_yaml
    sc.class_time = {}
    for cls_name, spec in (doc.get("class_time") or {}).items():
        sc.class_time[cls_name] = SupplyChain._parse_class_time(
            "doc", cls_name, spec)

    for name, spec in doc["nodes"].items():
        im = SupplyChain._parse_item_mass("doc", name, spec)
        sc.node(name, spec["level"], spec["class"], item_mass=im)

    for s in doc["statements"]:
        dist_params = {k: float(v) for k, v in s.items() if k not in _CORE}
        for k in _REQUIRED:
            if k not in s:
                raise ValueError(
                    f"statement {s['whole']}->{s['part']} missing required '{k}'")
        if float(s["best"]) == 0.0:
            raise ValueError(
                f"statement {s['whole']}->{s['part']} has best=0.0 (zero content)")
        unit = UNIT_BY_NAME[s["unit"]]
        from common.vocab import fill_quality
        quality = fill_quality(s.get("quality"))   # always the full six-dim vector
        lo = float(s["lo"]) if "lo" in s else None
        hi = float(s["hi"]) if "hi" in s else None
        sc.stmt(s["whole"], s["part"], float(s["best"]), lo, hi, unit,
                dist=s["dist"], dist_params=dist_params, quality=quality)

    sc._note = doc.get("note", "")
    return sc

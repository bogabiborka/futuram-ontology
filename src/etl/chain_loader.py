# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml"]
# ///
"""chain_loader — doc-level material canonicalisation for the ETL real path.
canonicalize_doc(doc) rewrites a scenario doc IN MEMORY (pure dict, no oracle/rdflib)
so every material class carries one intrinsic composition; called before composition_rdf.
"""
from collections import defaultdict


from . import canonicalize_materials as CM


def _material_parent_component(doc, level):
    """material node -> its COMPONENT-class context (from the structural m-c
    statement), keyed by component CLASS resolved to its YEAR BASE (chemistry is
    time-independent). Falls back to the material's own class with no comp parent."""
    from .doc_slices import _year_base
    cls = {n: spec["class"] for n, spec in doc["nodes"].items()}
    ct = doc.get("class_time") or {}

    def base_of(c):
        return _year_base(ct.get(c) or {}) or c

    comp_of = {}
    for s in doc["statements"]:
        if level.get(s["whole"]) == "Component" and \
           level.get(s["part"]) == "Material":
            comp_of[s["part"]] = base_of(cls[s["whole"]])
    return comp_of


def canonicalize_doc(doc):
    """Canonicalise a doc IN MEMORY so every material CLASS = ONE composition (MEAN
    of measured fractions, all elements kept); a class spanning >1 COMPONENT context
    SPLITS into <cls>_in_<comp> subclasses. Returns (doc, changes=[(node, old, new)])."""
    measured = CM.material_comps(doc)          # material node -> {elem: fraction}
    cls = {n: spec["class"] for n, spec in doc["nodes"].items()}
    level = {n: spec["level"] for n, spec in doc["nodes"].items()}
    comp_ctx = _material_parent_component(doc, level)

    # EVERY material node, not only those with element data: a node with no measured
    # e-m breakdown (a real source gap) carries no composition of its own and must
    # INHERIT its class's canonical make-up (filled from the class mean below).
    mat_nodes = [n for n, spec in doc["nodes"].items()
                 if spec["level"] == "Material"]

    # the REAL component contexts a class appears in (only nodes with a component
    # parent count; an orphan node must NOT invent a phantom context that splits).
    real_contexts = defaultdict(set)           # base_cls -> {component class}
    for mat in mat_nodes:
        if mat in comp_ctx:
            real_contexts[cls[mat]].add(comp_ctx[mat])

    # group material nodes by (authored class, component-class context): one make-up
    # per group, mean over nodes with element data. A class spanning >1 REAL context
    # splits; an orphan joins its class's sole context, else the class.
    groups = defaultdict(list)                 # (base_cls, comp_ctx) -> [node]
    for mat in mat_nodes:
        base = cls[mat]
        ctx = comp_ctx.get(mat)
        if ctx is None:                        # orphan: no component parent here
            only = real_contexts[base]
            ctx = next(iter(only)) if len(only) == 1 else base
        groups[(base, ctx)].append(mat)
    contexts_of = real_contexts

    node_target_class = {}                     # material node -> canonical class
    canon_comp = {}                            # canonical class -> {elem: mean}
    extra_subclass = {}                        # split subclass -> parent class
    changes = []
    for (base, ctx), group in groups.items():
        split = len(contexts_of[base]) > 1
        target_cls = f"{base}_in_{ctx}" if split else base
        if split:
            extra_subclass[target_cls] = [base]
        # canonical = elementwise MEAN over the group's nodes that HAVE measured
        # element data (empty-composition nodes inherit, they don't dilute).
        with_data = [mat for mat in group if measured.get(mat)]
        acc = defaultdict(float)
        for mat in with_data:
            for ec, v in measured[mat].items():
                acc[ec] += v
        canon_comp[target_cls] = ({ec: t / len(with_data) for ec, t in acc.items()}
                                  if with_data else {})
        for mat in group:
            node_target_class[mat] = target_cls
            if target_cls != cls[mat]:
                changes.append((mat, cls[mat], target_cls))

    # apply the (possibly split) class to each material node
    for mat, target_cls in node_target_class.items():
        doc["nodes"][mat]["class"] = target_cls
    # register split subclasses so aggregate() rolls them up to the parent
    if extra_subclass:
        doc.setdefault("subclass_of", {}).update(extra_subclass)

    # element NODE name to reuse when SYNTHESISING a statement for a node that
    # lacked one (so two nodes of a class never share an element node): keyed by
    # (material node, element class) -> existing element node name.
    elem_node = {}
    for s in doc["statements"]:
        if level.get(s["whole"]) == "Material" and \
           level.get(s["part"]) == "Element":
            elem_node[(s["whole"], cls[s["part"]])] = s["part"]

    # provenance/dist boilerplate to clone onto any synthesised statement
    template = next((s for s in doc["statements"]
                     if level.get(s["whole"]) == "Material"
                     and level.get(s["part"]) == "Element"), None)

    def _emit_canonical(mat, ec, frac):
        """A material->element statement at the canonical fraction, reusing the
        node's own element node if present else minting one."""
        en = elem_node.get((mat, ec))
        if en is None:
            en = f"{mat}_{ec}"
            doc["nodes"][en] = {"level": "Element", "class": ec}
            elem_node[(mat, ec)] = en
        base = dict(template) if template else {
            "unit": "kgkg", "dist": "uniform", "quality": {}}
        base.update({"whole": mat, "part": en, "best": round(frac, 9),
                     "lo": round(max(0.0, frac - 0.005), 9),
                     "hi": round(min(1.0, frac + 0.005), 9)})
        return base

    # rewrite each material->element statement to its class's canonical fraction
    # (the per-group MEAN). Every measured element survives; only product/year
    # noise within a (material, component) group is smoothed.
    kept = []
    have = defaultdict(set)                     # material node -> {element class}
    for s in doc["statements"]:
        w, p = s["whole"], s["part"]
        if level.get(w) == "Material" and level.get(p) == "Element":
            ec = cls[p]
            target = canon_comp.get(node_target_class.get(w), {})
            if ec in target:
                frac = target[ec]
                s["best"] = round(frac, 9)
                s["lo"] = round(max(0.0, frac - 0.005), 9)
                s["hi"] = round(min(1.0, frac + 0.005), 9)
                kept.append(s)
                have[w].add(ec)
            # else: element genuinely absent from this group's make-up — drop
        else:
            kept.append(s)

    # fill gaps: a material node missing any of its class's canonical elements
    # (an empty-composition node, or one measured with fewer elements) gets the
    # canonical statement synthesised, so every node of a class is consistent.
    for mat in mat_nodes:
        for ec, frac in canon_comp.get(node_target_class.get(mat), {}).items():
            if ec not in have[mat]:
                kept.append(_emit_canonical(mat, ec, frac))
                have[mat].add(ec)

    doc["statements"] = kept
    return doc, changes

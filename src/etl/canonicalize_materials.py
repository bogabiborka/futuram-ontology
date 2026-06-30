# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml"]
# ///
"""Canonicalise scenario-YAML materials to HONEST classes (one composition per
class): classify each material's element comp to an honest class + CANONICAL
fractions, rewrite node `class` + m->e best/lo/hi (structural c-p/m-c untouched).
"""
from __future__ import annotations

import sys
import pathlib

import yaml

# element symbol classes treated as the "primary" of a material family
PRIMARY = {"Copper", "Iron", "Carbon", "Nickel", "Aluminium", "Silver"}


def classify(comp):
    """comp: {element_class: fraction} -> (honest_class, canonical_comp), mapping a
    real composition to the nearest honest class with ONE fixed composition. An
    OVERSHOOTING material (sum > 1) is kept as a distinct, consistent class."""
    total = sum(comp.values())
    cu = comp.get("Copper", 0.0)
    ni = comp.get("Nickel", 0.0)
    fe = comp.get("Iron", 0.0)
    c = comp.get("Carbon", 0.0)
    al = comp.get("Aluminium", 0.0)
    ag = comp.get("Silver", 0.0)

    # --- overshoot preserved: elements sum clearly above 1 ---
    if total > 1.0 + 1e-6:
        # keep its shape but as a distinct honest class so it's self-consistent
        if cu > 0 and ni > 0:
            return "cupronickel_overshoot", dict(comp)
        return "overshootMaterial", dict(comp)

    # --- copper family ---
    if cu > 0 and cu >= max(ni, fe, c, al, ag):
        if ni <= 1e-9 and cu >= 0.999:
            return "pureCu", {"Copper": 1.0}
        if ni > 0 and cu >= 0.95:
            return "highCuAlloys", {"Copper": 0.99, "Nickel": 0.01}
        if ni > 0:                       # substantial Ni -> cupronickel
            return "cupronickel", {"Copper": 0.85, "Nickel": 0.15}
        return "pureCu", {"Copper": 1.0}

    # --- nickel material (a 'copper' node that is actually nickel) ---
    if ni > 0 and ni >= max(cu, fe, c, al, ag):
        return "NiAndNiAlloys", {"Nickel": 1.0}

    # --- iron / steel family ---
    if fe > 0 and fe >= max(cu, ni, c, al, ag):
        if c <= 1e-9 and fe >= 0.999:
            return "steelAndSteelAlloys", {"Iron": 1.0}
        if c > 0:
            return "lowCarbonSteel", {"Iron": 0.99, "Carbon": 0.01}
        return "steelAndSteelAlloys", {"Iron": 1.0}

    # --- aluminium family ---
    if al > 0 and al >= max(cu, ni, fe, c, ag):
        if al >= 0.999:
            return "AlAndAlAlloys", {"Aluminium": 1.0}
        # Al with a trace of something else
        return "AlAndAlAlloys", {"Aluminium": 1.0}

    # --- silver family ---
    if ag > 0:
        if cu > 0:
            return "AgCuAlloys", {"Silver": 0.5, "Copper": 0.5}
        return "AgAndAgAlloys", {"Silver": 1.0}

    # --- carbon / plastics family ---
    if c > 0 and c >= max(cu, ni, fe, al, ag):
        if cu > 0 or any(v > 0 for k, v in comp.items()
                         if k not in ("Carbon",)):
            return "plasticComposites", {"Carbon": 0.99, "Copper": 0.01}
        return "thermoplastics", {"Carbon": 1.0}

    # fallback: keep the dominant element as a pure material
    dom = max(comp, key=comp.get)
    return f"pure_{dom}", {dom: 1.0}


def material_comps(doc):
    """{material_node: {element_class: best_fraction}} from the YAML, using the
    node->class map to know which parts are Elements."""
    level = {n: spec["level"] for n, spec in doc["nodes"].items()}
    cls = {n: spec["class"] for n, spec in doc["nodes"].items()}
    out = {}
    for s in doc["statements"]:
        w, p = s["whole"], s["part"]
        if level.get(w) == "Material" and level.get(p) == "Element":
            out.setdefault(w, {})[cls[p]] = out.get(w, {}).get(cls[p], 0.0) \
                + float(s["best"])
    return out


def canonicalize(path):
    doc = yaml.safe_load(open(path))
    comps = material_comps(doc)
    cls = {n: spec["class"] for n, spec in doc["nodes"].items()}
    level = {n: spec["level"] for n, spec in doc["nodes"].items()}

    # rewrite each material's element statements to the canonical fractions
    new_class = {}            # material node -> honest class
    canon = {}                # material node -> {element_class: fraction}
    for mat, comp in comps.items():
        honest, canonical = classify(comp)
        new_class[mat] = honest
        canon[mat] = canonical

    # apply node class changes
    changes = []
    for mat, honest in new_class.items():
        old = cls[mat]
        if old != honest:
            changes.append((mat, old, honest))
        doc["nodes"][mat]["class"] = honest

    # rewrite material->element statements to canonical fractions; an element NOT
    # in the canonical comp is dropped (its statement removed).
    elem_node_of = {}         # (material, element_class) -> element node name
    for s in doc["statements"]:
        if level.get(s["whole"]) == "Material" and \
           level.get(s["part"]) == "Element":
            elem_node_of[(s["whole"], cls[s["part"]])] = s["part"]

    kept = []
    seen = set()
    for s in doc["statements"]:
        w, p = s["whole"], s["part"]
        if level.get(w) == "Material" and level.get(p) == "Element":
            ec = cls[p]
            target = canon.get(w, {})
            if ec in target:
                frac = target[ec]
                s["best"] = round(frac, 9)
                # tight symmetric band, clamped to >= 0 and <= 1
                s["lo"] = round(max(0.0, frac - 0.005), 9)
                s["hi"] = round(min(1.0, frac + 0.005), 9)
                kept.append(s)
                seen.add((w, ec))
            # else: drop this element (not in the canonical composition)
        else:
            kept.append(s)
    doc["statements"] = kept

    _dump_scenario(doc, path)
    return changes


class _FlowStmt(dict):
    """A statement dict rendered in compact flow style ({a: 1, ...})."""


def _dump_scenario(doc, path):
    """Write the scenario with statements (and their quality maps) in compact
    one-line flow style, like the hand-authored YAMLs; everything else block."""
    import copy
    d = copy.deepcopy(doc)
    d["statements"] = [_FlowStmt({**s, "quality": _FlowStmt(s.get("quality", {}))})
                       for s in d["statements"]]

    class _D(yaml.SafeDumper):
        pass

    _D.add_representer(_FlowStmt, lambda dr, data:
                       dr.represent_mapping("tag:yaml.org,2002:map", data,
                                            flow_style=True))
    with open(path, "w") as fh:
        yaml.dump(d, fh, Dumper=_D, sort_keys=False, default_flow_style=False,
                  width=4000, allow_unicode=True)


def main(argv=None):
    files = [pathlib.Path(a) for a in (argv or sys.argv[1:])]
    if not files:
        print("usage: canonicalize_materials.py FILE.yaml ...", file=sys.stderr)
        return 1
    for f in files:
        changes = canonicalize(f)
        print(f"{f.name}: {len(changes)} material reclass(es)")
        for mat, old, new in changes:
            print(f"    {mat}: {old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

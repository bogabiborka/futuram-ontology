"""PartOfPlugin — the part-of mass angle: component-in-product, material-in-product,
material-in-component fractions via fq:contains, plus the per-context minted unknown*
holder decomposition. Emits VALID RDF (remainders deduped within + against its `deps`).
"""
from __future__ import annotations

from collections import defaultdict

from rdflib import Graph, RDF, RDFS, OWL

from ..plugin import Plugin
from .. import emit_helpers as E
from ..dedup import dedup
from ..vocab import (FQ, FUT, LEVEL_CLASS, CLASS_LEVEL, UNKNOWN_FOR_LEVEL,
                     UNKNOWN_COMPONENT, UNKNOWN_MATERIAL, K_UNKNOWN_MIN,
                     PRODUCT, COMPONENT, MATERIAL, ELEMENT,
                     class_iri, local, next_level)


# --- rollup + constituent maps (inlined) ----------------------------------
def _rollup_over_subclasses(ctx, out, leaf_classes):
    ancestors = set()
    for cls in leaf_classes:
        ancestors |= (ctx.ancestors_of(cls) - {cls})
    remaining = set(ancestors)
    for _ in range(len(remaining) + 1):
        progressed = False
        for p in sorted(remaining):
            subs = sorted({c for c in out if p in ctx.superclasses.get(c, ())})
            if not subs:
                continue
            subs = ctx.parent_gate(p, True, subs)
            if subs is None:
                remaining.discard(p); progressed = True; continue
            if not subs:
                continue
            acc = defaultdict(list)
            for s in subs:
                for k, v in out[s].items():
                    acc[k].append(v)
            out[p] = {k: sum(vs) / len(subs) for k, vs in acc.items()}
            remaining.discard(p); progressed = True
        if not progressed:
            break
    return out


def _constituent_total_in_root(ctx, root, part_level, part_class, adj):
    total = [0.0]

    def walk(cur, acc):
        for part, frac in adj[cur]:
            f = acc * frac
            if ctx.node_level(part) == part_level and ctx.node_class(part) == part_class:
                total[0] += f
            else:
                walk(part, f)
    walk(root, 1.0)
    return total[0]


def _constituent_in_product(ctx, part_level):
    tops = ctx.top_instances()
    if not tops:
        return {}
    part_classes = ctx.classes_at_level(part_level)
    leaf_roots = defaultdict(list)
    for r in tops:
        leaf_roots[ctx.node_class(r)].append(r)
    adj = ctx.structural_adj()
    out = {}
    for pcls, roots in leaf_roots.items():
        n = len(roots)
        per = {}
        for pc in part_classes:
            acc = sum(_constituent_total_in_root(ctx, r, part_level, pc, adj)
                      for r in roots)
            amt = acc / n if n else 0.0
            if amt > 1e-12:
                per[pc] = amt
        out[pcls] = per
    return _rollup_over_subclasses(ctx, out, list(leaf_roots))


def _constituent_in_component(ctx, part_level=MATERIAL):
    comp_nodes = ctx.component_nodes()
    if not comp_nodes:
        return {}
    part_classes = ctx.classes_at_level(part_level)
    by_class = defaultdict(list)
    for nm, cc, root, _pc in comp_nodes:
        by_class[cc].append((nm, root))
    adj = ctx.structural_adj()
    out = {}
    for ccls, nodes in by_class.items():
        n = len(nodes)
        per = {}
        for pc in part_classes:
            acc = sum(_constituent_total_in_root(ctx, nm, part_level, pc, adj)
                      for nm, r in nodes)
            amt = acc / n if n else 0.0
            if amt > 1e-12:
                per[pc] = amt
        out[ccls] = per
    return _rollup_over_subclasses(ctx, out, list(by_class))


# --- inferred remainder content (inlined) ---------------------------------
def _inferred_unknown_content(ctx):
    per_root = defaultdict(lambda: defaultdict(float))
    for plan in ctx.unknowns():
        top, _frac = ctx.top_and_fraction(plan["whole"])
        fillers = list(plan["fillers"])
        first_level = fillers[0] if fillers else ctx.node_level(plan["part"])
        tail = tuple(fillers[1:])
        part_cls = ctx.node_class(plan["part"])
        per_root[top][(first_level, tail, part_cls)] += plan["amount"]
    tops = ctx.top_instances()
    if not tops:
        return {}
    leaf_roots = defaultdict(list)
    for r in tops:
        leaf_roots[ctx.node_class(r)].append(r)
    out = {}
    for cls_name, roots in leaf_roots.items():
        n = len(roots)
        acc = defaultdict(float)
        for r in roots:
            for key, amt in per_root[r].items():
                acc[key] += amt
        out[cls_name] = [
            {"first_level": fl, "tail_levels": list(tail), "part_class": pc,
             "amount": v / n}
            for (fl, tail, pc), v in acc.items() if n and v > 0
        ]
    return out


def _mint_unknown_holder(g, ctx, whole_cls, level, depth=0, *, parent_local=None):
    """One unknown holder, named unknown<Kind>_in_<parent> so there is exactly ONE per
    (kind, parent). The KIND is BOTH the subClassOf TYPE and IRI prefix (a parent can
    host a remainder at two levels at once). `parent_local` omitted = depth-0 whole."""
    from ..vocab import scope_span
    kind = UNKNOWN_FOR_LEVEL[level]            # the unknown* TYPE for this tier
    parent = parent_local if parent_local is not None else whole_cls
    holder = FUT[f"{kind}_in_{parent}"]
    g.add((holder, RDF.type, OWL.Class))
    g.add((holder, RDFS.subClassOf, FUT[kind]))
    g.add((holder, FQ.aggregationStrategy, FUT.RemainderStrategy))
    entry = ctx.class_time.get(whole_cls)
    if entry is None:
        slices = ctx.slices_of_base(whole_cls)
        if slices:
            spans = [scope_span(e) for e in slices]
            y0, y1 = min(s for s, _ in spans), max(e for _, e in spans)
            entry = {"year": y0} if y0 == y1 else {"start": y0, "end": y1}
    if entry is not None:
        E.scope(g, holder, entry)
    return holder


def _decompose_unknown(g, ctx, whole_cls, holder, holder_level, holder_mass, plans, depth):
    child_level = next_level(holder_level)
    if child_level is None:
        return
    child_level_iri = LEVEL_CLASS[child_level]
    terminal, deeper = [], []
    for p in plans:
        (deeper if p["tail"] else terminal).append(p)
    named_sum = 0.0
    by_part = defaultdict(float)
    for p in terminal:
        by_part[p["part_class"]] += p["amount"]
    for part_cls, amt_top in by_part.items():
        if ctx.class_level(part_cls) != child_level:
            continue
        frac = (amt_top / holder_mass) if holder_mass > 0 else 0.0
        frac = min(frac, 1.0)
        if frac <= 0:
            continue
        named_sum += frac
        E.amount(g, holder, holder, part_cls, frac, level_class=child_level_iri)
    if deeper:
        next_holder = _mint_unknown_holder(g, ctx, whole_cls, child_level,
                                           depth + 1, parent_local=local(holder))
        next_frac = 1.0 - named_sum
        if next_frac > 1e-9:
            next_mass = holder_mass * next_frac
            E.amount(g, holder, holder, local(next_holder), next_frac,
                     level_class=child_level_iri)
            peeled = [{"tail": p["tail"][1:], "part_class": p["part_class"],
                       "amount": p["amount"]} for p in deeper]
            _decompose_unknown(g, ctx, whole_cls, next_holder, child_level,
                               next_mass, peeled, depth + 1)
    else:
        gap = 1.0 - float(named_sum)
        if gap <= 1e-9:
            return
        filler = _mint_unknown_holder(g, ctx, whole_cls, child_level,
                                      depth + 1, parent_local=local(holder))
        E.amount(g, holder, holder, local(filler), gap, level_class=child_level_iri)
        _decompose_unknown(g, ctx, whole_cls, filler, child_level,
                           holder_mass * gap, [], depth + 1)


def _has_contains_child(g, whole_iri, element_iri_):
    for a in g.objects(whole_iri, FQ.contains):
        if (a, FQ.constituent, element_iri_) in g:
            return True
    return False


def _project_whole_part_map(g, ctx, mapping, level_class, *, unknown_class, inferred, seen):
    want = ctx.want
    for wcls, parts in mapping.items():
        if want is not None and wcls not in want:
            continue
        if not parts:
            if not (unknown_class is not None and (inferred or {}).get(wcls)):
                continue
            parts = {}
        w_iri = E.class_node(g, wcls)
        from .axis import AxisPlugin
        AxisPlugin._emit_time(g, ctx, w_iri, wcls)
        for pcls, amt in parts.items():
            E.amount(g, w_iri, w_iri, pcls, amt, level_class=level_class)
        if unknown_class is None:
            continue
        this_level = CLASS_LEVEL[level_class]
        unknown_already_named = unknown_class in parts
        gap = 1.0 - sum(float(a) for k, a in parts.items() if k != unknown_class)
        my_plans = [
            {"tail": p["tail_levels"], "part_class": p["part_class"], "amount": p["amount"]}
            for p in (inferred or {}).get(wcls, []) if p["first_level"] == this_level
        ]
        if gap <= 1e-9 and not my_plans:
            continue
        if not my_plans:
            if not unknown_already_named:
                E.amount(g, w_iri, w_iri, unknown_class, gap, level_class=level_class)
            continue
        holder_mass = gap if gap > 1e-9 else sum(p["amount"] for p in my_plans)
        holder = _mint_unknown_holder(g, ctx, wcls, this_level, depth=0)
        if (holder, FQ.contains, None) in g or _has_contains_child(g, w_iri, holder) \
                or _has_contains_child(seen, w_iri, holder):
            continue
        E.amount(g, w_iri, w_iri, local(holder), holder_mass, level_class=level_class)
        _decompose_unknown(g, ctx, wcls, holder, this_level, holder_mass, my_plans, depth=0)


class PartOfPlugin(Plugin):
    name = "partof"
    deps = ("elements", "component")   # see upstream for cross-plugin idempotency

    def project(self, ctx, upstream) -> Graph:
        g = Graph()
        if not ctx.want_touches_structural():
            return g
        inferred = _inferred_unknown_content(ctx)
        _project_whole_part_map(g, ctx, _constituent_in_product(ctx, COMPONENT),
                                LEVEL_CLASS[COMPONENT], unknown_class=UNKNOWN_COMPONENT,
                                inferred=inferred, seen=upstream)
        _project_whole_part_map(g, ctx, _constituent_in_product(ctx, MATERIAL),
                                LEVEL_CLASS[MATERIAL], unknown_class=UNKNOWN_MATERIAL,
                                inferred=inferred, seen=upstream)
        _project_whole_part_map(g, ctx, _constituent_in_component(ctx, MATERIAL),
                                LEVEL_CLASS[MATERIAL], unknown_class=UNKNOWN_MATERIAL,
                                inferred=inferred, seen=upstream)
        self._project_inferred_product_unknowns(g, ctx, inferred, seen=upstream)
        # valid output: one remainder per slot, within own graph AND vs upstream
        # (elements/component already emitted some whole's remainder).
        dedup(g, seen=upstream)
        return g

    @staticmethod
    def _project_inferred_product_unknowns(g, ctx, inferred, *, seen):
        want = ctx.want
        level_class = LEVEL_CLASS[PRODUCT]
        for wcls, plans in (inferred or {}).items():
            if want is not None and wcls not in want:
                continue
            my_plans = [
                {"tail": p["tail_levels"], "part_class": p["part_class"], "amount": p["amount"]}
                for p in plans if p["first_level"] == PRODUCT
            ]
            if not my_plans:
                continue
            w_iri = E.class_node(g, wcls)
            from .axis import AxisPlugin
            AxisPlugin._emit_time(g, ctx, w_iri, wcls)
            holder = _mint_unknown_holder(g, ctx, wcls, PRODUCT, depth=0)
            if (holder, FQ.contains, None) in g or _has_contains_child(g, w_iri, holder) \
                    or _has_contains_child(seen, w_iri, holder):
                continue
            holder_mass = sum(p["amount"] for p in my_plans)
            E.amount(g, w_iri, w_iri, local(holder), holder_mass, level_class=level_class)
            _decompose_unknown(g, ctx, wcls, holder, PRODUCT, holder_mass, my_plans, depth=0)

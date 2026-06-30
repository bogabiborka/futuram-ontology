"""ComponentPlugin — the component-class angle. Every component class gets its element
rows (rolled up over the subclass DAG), level/scope/itemMass/MC pointers, and balance;
plus a per-product contextual scope node ("<component> in <product>").
"""
from __future__ import annotations

from collections import defaultdict

from rdflib import Graph, Literal, XSD, RDF, OWL

from ..plugin import Plugin
from .. import emit_helpers as E
from ..balance import balance
from ..dedup import dedup
from ..vocab import FQ, FUT, LEVEL_CLASS, COMPONENT, ELEMENT, class_iri
from .axis import AxisPlugin
from .mc_pointers import McPointerPlugin


def _component_element_totals(ctx, comp_class, product_class=None, *,
                              comp_nodes=None, elem_classes=None):
    """{element_class: amount} for a component class (equal mean over the
    matching component nodes' top instances), optionally scoped to a product."""
    if comp_nodes is None:
        comp_nodes = ctx.component_nodes()
    nodes = [(nm, root) for (nm, cc, root, pc) in comp_nodes
             if cc == comp_class and (product_class is None or pc == product_class)]
    if not nodes:
        return {}
    if elem_classes is None:
        elem_classes = ctx.element_classes
    n = len(nodes)
    out = {}
    for ec in elem_classes:
        acc = sum(ctx.element_in_whole(nm, ec) for nm, _ in nodes)
        amt = acc / n if n else 0.0
        if amt > 1e-12:
            out[ec] = amt
    return out


def _component_element_uncertainty(ctx, comp_class, product_class=None, *,
                                   comp_nodes=None):
    """{element_class: relative_uncertainty} for a component class — the uncertainty
    twin of _component_element_totals, the reach RSS over the SAME nodes the value
    averaged. None values are skipped (no ruleset / no spread)."""
    if comp_nodes is None:
        comp_nodes = ctx.component_nodes()
    nodes = [nm for (nm, cc, _root, pc) in comp_nodes
             if cc == comp_class and (product_class is None or pc == product_class)]
    if not nodes:
        return {}
    out = {}
    for ec in ctx.element_classes:
        u = ctx.element_uncertainty_over_nodes(nodes, ec)
        if u is not None:
            out[ec] = u
    return out


def _component_element_dq(ctx, comp_class, product_class=None, *,
                          comp_nodes=None):
    """{element_class: (mean_dq, dqs)} for a component class — the DQ twin of
    _component_element_totals, over the SAME nodes the value averaged. None entries
    are skipped (no ruleset / no DQ)."""
    if comp_nodes is None:
        comp_nodes = ctx.component_nodes()
    nodes = [nm for (nm, cc, _root, pc) in comp_nodes
             if cc == comp_class and (product_class is None or pc == product_class)]
    if not nodes:
        return {}
    out = {}
    for ec in ctx.element_classes:
        dq = ctx.element_dq_over_nodes(nodes, ec)
        if dq is not None:
            out[ec] = dq
    return out


def _rollup_dq_over_subclasses(ctx, dq_out, best_out, leaf_classes):
    """Ancestor element DQ = the EQUAL-mean rollup's DQ: parent mean_dq = subclass
    mean_dqs weighted by the absolute element mass each contributes (best_out), dqs =
    worst (max). Mirrors _rollup_uncertainty_over_subclasses' gating; mutates dq_out."""
    ancestors = set()
    for cls in leaf_classes:
        ancestors |= (ctx.ancestors_of(cls) - {cls})
    remaining = set(ancestors)
    for _ in range(len(remaining) + 1):
        progressed = False
        for p in sorted(remaining):
            subs = sorted({c for c in best_out if p in ctx.superclasses.get(c, ())})
            if not subs:
                continue
            subs = ctx.parent_gate(p, True, subs)
            if subs is None:
                remaining.discard(p); progressed = True; continue
            if not subs:
                continue
            per = {}
            for ec in {e for s in subs for e in best_out.get(s, {})}:
                wsum = 0.0
                wtot = 0.0
                worst = None
                for s in subs:
                    best_s = best_out.get(s, {}).get(ec)
                    dq_s = dq_out.get(s, {}).get(ec)
                    if best_s is None or dq_s is None:
                        continue
                    mean_s, dqs_s = dq_s
                    wsum += best_s * mean_s
                    wtot += best_s
                    worst = dqs_s if worst is None else max(worst, dqs_s)
                if worst is not None and wtot > 0:
                    per[ec] = (wsum / wtot, worst)
            dq_out[p] = per
            remaining.discard(p); progressed = True
        if not progressed:
            break
    return dq_out


def _rollup_uncertainty_over_subclasses(ctx, unc_out, best_out, leaf_classes):
    """Ancestor element uncertainties = the EQUAL-mean rollup's uncertainty: parent
    relative-u = RSS(subclass ABSOLUTE element sigmas) / k made relative to the parent
    best. Mirrors _rollup_over_subclasses' gating, reading best_out. Mutates unc_out."""
    ancestors = set()
    for cls in leaf_classes:
        ancestors |= (ctx.ancestors_of(cls) - {cls})
    remaining = set(ancestors)
    for _ in range(len(remaining) + 1):
        progressed = False
        for p in sorted(remaining):
            subs = sorted({c for c in best_out if p in ctx.superclasses.get(c, ())})
            if not subs:
                continue
            subs = ctx.parent_gate(p, True, subs)
            if subs is None:
                remaining.discard(p); progressed = True; continue
            if not subs:
                continue
            per = {}
            for ec in {e for s in subs for e in best_out.get(s, {})}:
                # a subclass with a best but NO uncertainty for this element has
                # zero spread on it -> abs_sigma 0 (it still counts toward the mean's
                # k, dampening the parent's relative u), NOT dropped.
                abs_sigmas = []
                for s in subs:
                    best_s = best_out.get(s, {}).get(ec)
                    if best_s is None:
                        continue
                    rel_s = unc_out.get(s, {}).get(ec) or 0.0
                    abs_sigmas.append(best_s * rel_s)
                parent_best = best_out.get(p, {}).get(ec)
                if abs_sigmas and parent_best:
                    k = len(subs)
                    sig = (sum(s * s for s in abs_sigmas) ** 0.5) / k
                    per[ec] = sig / parent_best
            unc_out[p] = per
            remaining.discard(p); progressed = True
        if not progressed:
            break
    return unc_out


def _rollup_over_subclasses(ctx, out, leaf_classes):
    """Ancestor entries = EQUAL mean of subclasses (fixpoint), gated by
    ctx.parent_gate — mirrors aggregate(). Mutates and returns `out`."""
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


class ComponentPlugin(Plugin):
    name = "component"
    deps = ("elements",)        # see ElementAmounts' remainders to dedup against

    def project(self, ctx, upstream) -> Graph:
        g = Graph()
        want = ctx.want
        comp_nodes = ctx.component_nodes()
        elem_classes = ctx.element_classes
        comp_classes = {cc for (_, cc, _, _) in comp_nodes}
        level_iri = LEVEL_CLASS[ELEMENT]

        elem_out = {}
        unc_out = {}
        dq_out = {}
        for cc in sorted(comp_classes):
            totals = _component_element_totals(ctx, cc, comp_nodes=comp_nodes,
                                               elem_classes=elem_classes)
            if totals:
                elem_out[cc] = totals
                unc_out[cc] = _component_element_uncertainty(
                    ctx, cc, comp_nodes=comp_nodes)
                dq_out[cc] = _component_element_dq(ctx, cc, comp_nodes=comp_nodes)
        _rollup_over_subclasses(ctx, elem_out, list(comp_classes))
        # uncertainty rolls up over the SAME subclass tree (RSS of subclass sigmas).
        _rollup_uncertainty_over_subclasses(ctx, unc_out, elem_out,
                                            list(comp_classes))
        # DQ rolls up the SAME way (value-weighted mean DQ, worst DQS).
        _rollup_dq_over_subclasses(ctx, dq_out, elem_out, list(comp_classes))

        for cc in sorted(elem_out):
            if want is not None and cc not in want:
                continue
            cls_iri = E.class_node(g, cc)
            totals = elem_out[cc]
            AxisPlugin._emit_time(g, ctx, cls_iri, cc, level=COMPONENT)
            im = ctx.item_mass.get(cc)
            if im is not None:
                g.add((cls_iri, FQ.itemMass, Literal(float(im), datatype=XSD.double)))
            McPointerPlugin._emit_pointers(g, ctx, cls_iri, cc)
            for ec, amt in sorted(totals.items()):
                rel_u = unc_out.get(cc, {}).get(ec)
                dq = dq_out.get(cc, {}).get(ec)
                mean_dq, dqs = dq if dq is not None else (None, None)
                E.amount(g, cls_iri, cls_iri, ec, amt, level_class=level_iri,
                         rel_u=rel_u, mean_dq=mean_dq, dqs=dqs)
            balance(g, cls_iri, sum(totals.values()), level_iri)

        # per-product contextual scopes ("<component> in <product>")
        for cc in sorted(comp_classes):
            cls_iri = class_iri(cc)
            products = {pc for (_, c, _, pc) in comp_nodes if c == cc}
            for pc in sorted(products):
                if want is not None and cc not in want and pc not in want:
                    continue
                scoped = _component_element_totals(ctx, cc, product_class=pc,
                                                   comp_nodes=comp_nodes,
                                                   elem_classes=elem_classes)
                if not scoped:
                    continue
                # the exact nodes this scoped value averaged — the uncertainty must
                # be the reach RSS over the SAME set (element_uncertainty_over_nodes).
                scoped_nodes = [nm for (nm, c, _r, p) in comp_nodes
                                if c == cc and p == pc]
                rep = FQ[f"{cc}_in_{pc}"]
                g.add((rep, RDF.type, OWL.Class))
                g.add((rep, RDF.type, cls_iri))
                g.add((rep, FUT.partOf, class_iri(pc)))
                cc_entry = ctx.class_time.get(cc)
                if cc_entry is not None:
                    E.scope(g, rep, cc_entry)
                # itemMass scoped to this product context: the mean instance mass of
                # the nodes actually in `pc`, not the component class mean (which
                # averages over all contexts). The absolute kg = fraction x itemMass
                # is only correct against the context-scoped mass.
                scoped_masses = [m for m in
                                 (ctx.index.item_mass.get(nm) for nm in scoped_nodes)
                                 if m is not None]
                if scoped_masses:
                    g.add((rep, FQ.itemMass,
                           Literal(float(sum(scoped_masses) / len(scoped_masses)),
                                   datatype=XSD.double)))
                for ec, amt in scoped.items():
                    rel_u = ctx.element_uncertainty_over_nodes(scoped_nodes, ec)
                    dq = ctx.element_dq_over_nodes(scoped_nodes, ec)
                    mean_dq, dqs = dq if dq is not None else (None, None)
                    E.amount(g, rep, rep, ec, amt, level_class=level_iri,
                             rel_u=rel_u, mean_dq=mean_dq, dqs=dqs)
                balance(g, rep, sum(scoped.values()), level_iri)

        # per-product contextual scopes for the PARENT GROUPS ("embedded electronics in
        # <vehicle>"): a group COMPOSES its part-subclasses, so its content in a product
        # is the MASS-WEIGHTED SUM of its children's scoped content (itemMass = Σ kids).
        self._emit_group_scopes(g, ctx, comp_nodes, elem_classes, level_iri,
                                want=want)
        dedup(g, seen=upstream)       # drop remainders ElementAmounts already emitted
        return g

    @staticmethod
    def _emit_group_scopes(g, ctx, comp_nodes, elem_classes, level_iri, *, want):
        """Mint <group>_in_<product> scope nodes for every declared parent group of the
        leaf component classes (mass-weighted SUM-of-parts roll-up over the group's
        descendant leaf nodes in each product; conserving: frac x itemMass == Σ child kg)."""
        # leaf component class -> the products it appears in, and its nodes-per-product
        leaf_classes = {cc for (_, cc, _, _) in comp_nodes}
        # every declared ancestor of a leaf that is itself a component group
        groups = set()
        for cc in leaf_classes:
            groups |= (ctx.ancestors_of(cc) - {cc})
        groups -= leaf_classes          # a group may also be a leaf elsewhere; skip overlap
        for grp in sorted(groups):
            if ctx.class_level(grp) != COMPONENT:
                continue
            # the leaf descendant nodes of this group, grouped by product
            by_product = defaultdict(list)
            for (nm, cc, _root, pc) in comp_nodes:
                if grp in ctx.ancestors_of(cc):     # cc is grp or below grp
                    by_product[pc].append(nm)
            for pc, nodes in sorted(by_product.items()):
                if want is not None and grp not in want and pc not in want:
                    continue
                masses = [ctx.index.item_mass.get(nm) for nm in nodes]
                masses = [m for m in masses if m is not None]
                tot_mass = sum(masses)
                if tot_mass <= 0:
                    continue
                # mass-weighted element fraction = Σ(child_frac·child_mass) / Σ mass
                fracs = {}
                for ec in elem_classes:
                    kg = sum(ctx.element_in_whole(nm, ec) * (ctx.index.item_mass.get(nm) or 0.0)
                             for nm in nodes)
                    if kg > 1e-12:
                        fracs[ec] = kg / tot_mass
                if not fracs:
                    continue
                rep = FQ[f"{grp}_in_{pc}"]
                g.add((rep, RDF.type, OWL.Class))
                g.add((rep, RDF.type, class_iri(grp)))
                g.add((rep, FUT.partOf, class_iri(pc)))
                grp_entry = ctx.class_time.get(grp)
                if grp_entry is not None:
                    E.scope(g, rep, grp_entry)
                g.add((rep, FQ.itemMass, Literal(float(tot_mass), datatype=XSD.double)))
                # mass-weighted uncertainty: fraction was Σ(frac·mass)/Σmass, so
                # uncertainty must weight each node's path terms by mass/tot_mass too.
                node_weights = {nm: (ctx.index.item_mass.get(nm) or 0.0) for nm in nodes}
                for ec, frac in fracs.items():
                    rel_u = ctx.element_uncertainty_over_nodes_weighted(
                        nodes, node_weights, ec)
                    dq = ctx.element_dq_over_nodes(nodes, ec)
                    mean_dq, dqs = dq if dq is not None else (None, None)
                    E.amount(g, rep, rep, ec, frac, level_class=level_iri,
                             rel_u=rel_u, mean_dq=mean_dq, dqs=dqs)
                balance(g, rep, sum(fracs.values()), level_iri)

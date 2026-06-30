# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///
"""Audit the futuram-hierarchy ABox (punned class-individuals) for two rules:
Rule T (time scope on Product/Component) and Rule S (composition statement or
aggregation strategy on P/C/Material; Elements exempt). Reports which rule fails.

Usage:
    uv run scripts/audit_hierarchy_abox.py                       # ABox alone
    uv run scripts/audit_hierarchy_abox.py --composition F.ttl…  # combined
    uv run scripts/audit_hierarchy_abox.py --strict              # exit 1 on findings
"""
from __future__ import annotations

import argparse
import pathlib
from collections import defaultdict

from rdflib import Graph, Namespace, RDF, RDFS, OWL

ROOT = pathlib.Path(__file__).resolve().parent.parent
HIERARCHY = ROOT / "ontology" / "tbox" / "futuram-hierarchy.ttl"
TBOX = ROOT / "ontology" / "tbox" / "composition-statement.ttl"

FUT = Namespace("https://www.purl.org/futuram#")
LEVEL_ROOTS = ("Product", "Component", "Material", "Element")


def _local(iri):
    return str(iri).rsplit("#", 1)[-1]


def load_graphs(composition_paths):
    hier = Graph()
    hier.parse(HIERARCHY, format="turtle")
    g = Graph()
    g += hier
    g.parse(TBOX, format="turtle")          # annotations/level edges only
    comp = Graph()
    for p in composition_paths:
        comp.parse(p, format="turtle")
    return hier, g, comp


def superclass_map(g):
    sup = defaultdict(set)
    for s, o in g.subject_objects(RDFS.subClassOf):
        if isinstance(s, type(o)) and str(s).startswith(str(FUT)) \
                and str(o).startswith(str(FUT)):
            sup[_local(s)].add(_local(o))
    return sup


def roots_of(cls, sup, _memo=None):
    """The set of level roots reachable from `cls` via rdfs:subClassOf*."""
    if _memo is None:
        _memo = {}
    if cls in LEVEL_ROOTS:
        return {cls}
    if cls in _memo:
        return _memo[cls]
    _memo[cls] = set()                    # cycle guard
    found = set()
    for parent in sup.get(cls, ()):
        found |= roots_of(parent, sup, _memo)
    _memo[cls] = found
    return found


def audit(hier, g, comp):
    sup = superclass_map(g)
    # the taxonomy ABox = classes declared in the HIERARCHY file only (the
    # merged graph also holds TBox classes like CompositionStatement, which
    # are not taxonomy members and must not be audited)
    all_classes = sorted({_local(s) for s in hier.subjects(RDF.type, OWL.Class)
                          if str(s).startswith(str(FUT))})

    time_scoped = {_local(s) for s in g.subjects(FUT.referenceYear, None)} | \
                  {_local(s) for s in g.subjects(FUT.hasReferencePeriod, None)}
    for s in comp.subjects(FUT.referenceYear, None):
        time_scoped.add(_local(s))
    for s in comp.subjects(FUT.hasReferencePeriod, None):
        time_scoped.add(_local(s))

    has_strategy = {_local(s)
                    for s in g.subjects(FUT.hasAggregationStrategy, None)} | \
                   {_local(s)
                    for s in comp.subjects(FUT.hasAggregationStrategy, None)}

    # classes whose instances carry at least one composition statement
    # (only meaningful when composition graphs were supplied)
    stmt_classes = set()
    for stmt in comp.subjects(RDF.type, FUT.CompositionStatement):
        for whole in comp.objects(stmt, FUT.statementWhole):
            for cls in comp.objects(whole, RDF.type):
                if str(cls).startswith(str(FUT)):
                    stmt_classes.add(_local(cls))

    # classes referenced by composition data but unknown to the hierarchy ABox
    referenced = set(stmt_classes)
    for stmt in comp.subjects(RDF.type, FUT.CompositionStatement):
        for part in comp.objects(stmt, FUT.statementPart):
            for cls in comp.objects(part, RDF.type):
                if str(cls).startswith(str(FUT)):
                    referenced.add(_local(cls))
    comp_sup = superclass_map(comp)

    memo = {}
    rows = []
    for cls in all_classes:
        if cls in LEVEL_ROOTS:
            continue
        roots = roots_of(cls, sup, memo)
        findings = []
        if not roots:
            findings.append("STRUCT: unreachable from any level root")
        elif len(roots) > 1:
            findings.append(f"STRUCT: multiple level roots {sorted(roots)}")
        level = next(iter(roots)) if len(roots) == 1 else None

        if level in ("Product", "Component"):
            slices_of = {c for c, parents in sup.items() if cls in parents} \
                        | {c for c, parents in comp_sup.items()
                           if cls in parents}
            has_scoped_sub = bool(slices_of & time_scoped)
            if cls not in time_scoped and not has_scoped_sub:
                findings.append("RULE-T: no time scope and no time-scoped "
                                "subclass")
        if level in ("Product", "Component", "Material"):
            if cls not in has_strategy and cls not in stmt_classes:
                findings.append("RULE-S: no aggregation strategy and no own "
                                "composition statement")
        if findings:
            rows.append((cls, level or "?", findings))

    undefined = sorted(c for c in referenced
                       if c not in all_classes and c not in LEVEL_ROOTS)
    return rows, undefined


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--composition", nargs="*", default=[],
                    help="composition-statement TTL file(s) for the combined "
                         "two-ABox audit")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when any finding is reported")
    ap.add_argument("--rule", choices=["T", "S"],
                    help="restrict the report to one rule")
    args = ap.parse_args()

    hier, g, comp = load_graphs(args.composition)
    rows, undefined = audit(hier, g, comp)

    if args.rule:
        want = f"RULE-{args.rule}"
        rows = [(c, lv, [f for f in fs if f.startswith(want)])
                for c, lv, fs in rows]
        rows = [(c, lv, fs) for c, lv, fs in rows if fs]

    by_rule = defaultdict(int)
    for cls, level, findings in rows:
        for f in findings:
            by_rule[f.split(":")[0]] += 1
        print(f"{cls:40s} [{level:9s}] {'; '.join(findings)}")

    if undefined:
        print()
        for c in undefined:
            print(f"{c:40s} [combined ] UNDEFINED: referenced by composition "
                  f"data but absent from the hierarchy ABox")

    print()
    print(f"classes with findings: {len(rows)}"
          + (f" + {len(undefined)} undefined-in-hierarchy" if undefined else ""))
    for rule, n in sorted(by_rule.items()):
        print(f"  {rule}: {n}")
    if args.strict and (rows or undefined):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

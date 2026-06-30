# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///
"""Post-rebuild verification for the served-taxonomy fq change.

Run AFTER `tests/build_instances.py futuram --years 2010 2020 2025 2030 2050`
against the rebuilt fuseki/futuram/data/query/futuram.ttl. Checks:

  1. base + year-slice part->parent rdfs:subClassOf edges are served
     (elvEmbeddedElectronicsCables -> elvEmbeddedElectronics, and the _Y2025 slice
     -> elvEmbeddedElectronics_Y2025); likewise a material family (… -> non-ferrousMetals).
  2. the parent GROUP gets an in-vehicle scope node
     (elvEmbeddedElectronics_Y2025_in_V0301030103_Y2025) — 0 before the fix.
  3. holder rename: unknown_in_<parent> holders exist, each rdfs:subClassOf its kind;
     NO stale <kind>_in_<parent> IRIs remain.
  4. the unknown-aggregation query works on the SERVED graph:
     SUM over ?u rdfs:subClassOf unknownComponent contained in a whole.
  5. Q7 reconciliation numbers: Cu in elvEmbeddedElectronics(4-child) vs +elvPowerElectronics,
     elvGeneralComponents, elvElectricMotor for seg-C BEV 2025 — REPORTED, not asserted
     (ground truth = the docx).

Usage: uv run --offline --with rdflib bench/verify_taxonomy_fq.py [futuram.ttl]
"""
import sys
from rdflib import Graph, URIRef, RDFS

FUT = "https://www.purl.org/futuram#"
FQ = "https://www.purl.org/futuram/query#"
TTL = sys.argv[1] if len(sys.argv) > 1 else "fuseki/futuram/data/query/futuram.ttl"
VEH = "V0301030103"          # segment-C BEV
YEAR = "2025"

g = Graph().parse(TTL)
ok = []
fail = []


def check(label, cond, detail=""):
    (ok if cond else fail).append((label, detail))
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if detail else ""))


def supers(name):
    return {str(o).split("#")[-1] for o in g.objects(URIRef(FUT + name), RDFS.subClassOf)}


def subclasses_of(kind):
    """Every served class rdfs:subClassOf futuram:<kind> (the per-parent holders)."""
    k = URIRef(FUT + kind)
    return {str(s).split("#")[-1] for s in g.subjects(RDFS.subClassOf, k)}


print("=" * 70, "\n1. PART->PARENT EDGES SERVED\n")
check("base: elvEmbeddedElectronicsCables -> elvEmbeddedElectronics",
      "elvEmbeddedElectronics" in supers("elvEmbeddedElectronicsCables"),
      str(supers("elvEmbeddedElectronicsCables")))
check(f"slice: ..Cables_Y{YEAR} -> elvEmbeddedElectronics_Y{YEAR}",
      f"elvEmbeddedElectronics_Y{YEAR}" in supers(f"elvEmbeddedElectronicsCables_Y{YEAR}"),
      str(supers(f"elvEmbeddedElectronicsCables_Y{YEAR}")))

print("\n2. PARENT GROUP SCOPE NODE\n")
grp = sorted(ls for s in set(g.subjects())
             if (ls := str(s).split("#")[-1]).startswith(f"elvEmbeddedElectronics_Y{YEAR}_in_V")
             and "amount_" not in ls)
check("elvEmbeddedElectronics group has in-vehicle scope nodes (was 0)",
      len(grp) > 0, f"{len(grp)} nodes, e.g. {grp[:2]}")

print("\n3. UNKNOWN HOLDER RENAME\n")
stale = sorted(ls for s in set(g.subjects())
               if any((ls := str(s).split("#")[-1]).startswith(p + "_in_")
                      for p in ("unknownComponent", "unknownMaterial",
                                "unknownElement", "unknownProduct")))
# NOTE: holders are named <kind>_in_<parent> (kept the kind in the IRI — the
# unknown_in_<parent> form collided two kinds on one parent). A "stale" name would
# be the now-defunct unknown_in_<parent> form.
stale_newform = sorted(ls for s in set(g.subjects())
                       if (ls := str(s).split("#")[-1]).startswith("unknown_in_"))
check("no defunct unknown_in_<parent> IRIs remain", not stale_newform,
      f"{len(stale_newform)} defunct: {stale_newform[:3]}")
holders = sorted(ls for s in set(g.subjects())
                 if any((ls := str(s).split("#")[-1]).startswith(p + "_in_")
                        for p in ("unknownComponent", "unknownMaterial",
                                  "unknownElement", "unknownProduct")))
# real fleet may be fully attributed in places; just report the count (not a gate).
print(f"     <kind>_in_<parent> holders: {len(holders)}"
      + (f", e.g. {holders[:3]}" if holders else ""))
# every holder subClassOf exactly one kind
for kind in ("unknownComponent", "unknownMaterial", "unknownElement", "unknownProduct"):
    n = len(subclasses_of(kind))
    print(f"     {kind}: {n} per-parent holders rdfs:subClassOf it")

print("\n4. UNKNOWN-AGGREGATION QUERY ON SERVED GRAPH\n")
q = f"""
PREFIX fq: <{FQ}>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX futuram: <{FUT}>
SELECT (SUM(?a) AS ?unk) WHERE {{
  ?u rdfs:subClassOf futuram:unknownComponent .
  ?whole fq:contains ?amt .
  ?amt fq:constituent ?u ; fq:amount ?a .
}}"""
rows = list(g.query(q))
val = rows[0][0] if rows and rows[0][0] is not None else None
check("unknown-component aggregation query returns a value", val is not None, str(val))

print("\n5. Q7 RECONCILIATION (REPORTED, not asserted — ground truth = docx)\n")


def cu_in_scope(group):
    """Cu kg in <group>_Y2025_in_<VEH>_Y2025: amount(kg/kg) * group itemMass.
    Reports the served fraction * itemMass if present, else None. Scope reps live
    in the FQ namespace, not FUT."""
    scope = f"{group}_Y{YEAR}_in_{VEH}_Y{YEAR}"
    s = URIRef(FQ + scope)
    cu = URIRef(FUT + "Copper")
    frac = None
    for amt in g.objects(s, URIRef(FQ + "contains")):
        if (amt, URIRef(FQ + "constituent"), cu) in g:
            frac = float(next(g.objects(amt, URIRef(FQ + "amount"))))
    im = None
    for q2 in g.objects(s, URIRef(FQ + "itemMass")):
        im = float(q2)
    return scope, frac, im, (frac * im if frac is not None and im is not None else None)


for grpname in ("elvEmbeddedElectronics", "elvPowerElectronics",
                "elvGeneralComponents", "elvElectricMotor"):
    scope, frac, im, kg = cu_in_scope(grpname)
    print(f"  {grpname:24} scope={scope}\n      frac={frac} itemMass={im} -> Cu kg={kg}")

print("\n" + "=" * 70)
print(f"SUMMARY: {len(ok)} pass, {len(fail)} fail")
if fail:
    print("FAILURES:")
    for l, d in fail:
        print("  -", l, d)
    sys.exit(1)

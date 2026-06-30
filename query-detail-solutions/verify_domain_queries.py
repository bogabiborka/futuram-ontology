#!/usr/bin/env python3
"""Pure SPARQL query-applier: run one query per domain question against BOTH
backends and compare each result to the SI value.

The .rq solutions are foldered to MIRROR the domain testcase split:
    competency/{fq,composition}/  -> the SI's ten NL competency questions
    paper/{fq,composition}/       -> the four special SPARQL-query questions (SI-5/6/7
                                     + embedded controllers)
Two endpoints back the two backend sub-folders:
    <group>/fq/           -> the fq  /query        view  (precomputed, class-level)
    <group>/composition/  -> the raw /composition  view  (statement-tree traversal)

There is NO domain logic and NO arithmetic in Python — every answer (including the
RSS uncertainty via afn:sqrt, and the composition-side DQS uncertainty formula) is
computed inside the SPARQL query. This script only: POSTs each query to its
endpoint, reads the number back, compares it to the SI value, and writes the
markdown. Comparison is by EQUALITY (a result row not in the golden is flagged as
EXTRA), not subset — so a truncated golden (e.g. 10 rows where the data has 13) fails
rather than passing on a "every expected key present" check.

Endpoints (override the base with FUSEKI_BASE; default the bench Fuseki):
    http://localhost:47040/query/sparql
    http://localhost:47040/composition/sparql

Run:
    uv run --with requests python query-detail-solutions/verify_domain_queries.py
"""
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = os.environ.get("FUSEKI_BASE", "http://localhost:47040")
HERE = os.path.dirname(os.path.abspath(__file__))

# The .rq solutions are foldered to MIRROR the domain testcase split:
#   competency/<be>/  — the SI's ten NL competency questions (domain-competency.yaml)
#   paper/<be>/        — the four special SPARQL-query questions (domain-sparql.yaml)
# Each backend (fq | composition) is the sub-folder inside the group. A case's group
# is given by GROUP_OF below; the query path is <group>/<be_dir>/<id>.rq.
BACKENDS = [
    ("fq",          "fq",          BASE + "/query/sparql"),
    ("composition", "composition", BASE + "/composition/sparql"),
]

# Which group each case belongs to (folder under query-detail-solutions/).
PAPER_CASES = {
    "cu_by_bev_segment_2030",
    "al5xxx_by_bev_segment_2020",
    "elements_embedded_electronics_std_bev_2030",
    "crm_recovered_embedded_controllers_std_bev_2025",
}
def group_of(cid):
    return "paper" if cid in PAPER_CASES else "competency"

# SI-expected values, shared by both backends (the answer is the same; only the
# query shape differs). "single" => one number (+ optional uncertainty); "multi"
# => {localname-substring: value}. Comparison only — never used to compute.
CASES = {
    "al_diesel_clioclass_2025": dict(
        q="Theoretical total mass of aluminium in a diesel Clio-class car, 2025.",
        value=113.05, uncertainty=11.8),
    "cu_petrol_clioclass_2025": dict(
        q="Theoretical total mass of copper in a petrol Clio-class car, 2025.",
        value=7.57, uncertainty=0.54),
    "cu_hev_clioclass_2025": dict(
        q="Theoretical total mass of copper in a hybrid (HEV) Clio-class car, 2025.",
        value=29.74, uncertainty=3.5),
    "crm_total_bev_segmentD_2020": dict(
        q="Total critical raw material content of a segment D BEV, 2020 (no battery component in data).",
        value=349.27, uncertainty=27.79,
        note="Copper IN (critical); Carbon OUT (graphite/coking-coal are the CRMs)."),
    "crm_total_phev_segmentC_2020": dict(
        q="Total critical raw material content of a segment C PHEV, 2020.",
        value=214.10, uncertainty=16.77,
        note="SOLE SI EXCEPTION: SI 176.16 omitted critical copper; corrected to 214.10."),
    "ree_in_motor_bev_segmentC_2025": dict(
        q="Total rare-earth content in the permanent-magnet motor of a segment C BEV, 2025.",
        value=0.86, uncertainty=0.11),
    "cu_distribution_bev_segmentC_2025": dict(
        q="Copper distributed across the components of a segment C BEV, 2025.",
        multi={"ElectricMotor": 31.185, "EmbeddedElectronics": 10.43, "GeneralComponents": 3.97},
        note="'battery' dropped from the question — no battery component in the data."),
    "al_alloy_demand_hev_segmentB_2020": dict(
        q="Al-alloy demand by alloy type in a B-segment HEV, 2020.",
        multi={"castAlAlloy": 66.52, "5xxxAlAlloy": 24.98, "6xxxAlAlloy": 11.75, "2xxxAlAlloy": 1.94}),
    "crm_recovered_embedded_electronics_std_bev_2025": dict(
        q="Recoverable CRM quantities in the embedded electronics of a standard BEV, 2025.",
        multi={"Copper": 11.58, "Aluminium": 0.980439, "Palladium": 0.001995}),
    "cu_by_bev_segment_2030": dict(
        q="Copper in each BEV passenger-body segment, 2030.",
        multi={"V0301030206": 78.27, "V0301030205": 70.88, "V0301030106": 67.22,
               "V0301030105": 61.70, "V0301030204": 57.38, "V0301030104": 53.93,
               "V0301030203": 51.15, "V0301030000": 49.73, "V0301030103": 46.05,
               "V0301030202": 45.59, "V0301030102": 42.89, "V0301030201": 39.04,
               "V0301030101": 36.46},
        multi_uncertainty={"V0301030206": 9.01, "V0301030205": 8.04, "V0301030106": 6.23,
                           "V0301030105": 5.71, "V0301030204": 6.52, "V0301030104": 5.17,
                           "V0301030203": 6.01, "V0301030000": 3.22, "V0301030103": 4.68,
                           "V0301030202": 5.65, "V0301030102": 4.52, "V0301030201": 4.84,
                           "V0301030101": 2.46}),
    "al5xxx_by_bev_segment_2020": dict(
        q="5xxx-series aluminium alloy in each BEV passenger-body segment, 2020.",
        multi={"V0301030206": 127.2, "V0301030205": 113.19, "V0301030106": 106.0,
               "V0301030105": 94.32, "V0301030204": 75.42, "V0301030104": 62.85,
               "V0301030000": 47.06, "V0301030203": 45.2, "V0301030103": 37.67,
               "V0301030202": 28.72, "V0301030102": 24.98, "V0301030201": 17.31,
               "V0301030101": 15.74},
        multi_uncertainty={"V0301030206": 22.03, "V0301030205": 19.6, "V0301030106": 18.36,
                           "V0301030105": 16.34, "V0301030204": 13.06, "V0301030104": 10.56,
                           "V0301030000": 6.79, "V0301030203": 7.82, "V0301030103": 6.52,
                           "V0301030202": 4.97, "V0301030102": 4.33, "V0301030201": 3.0,
                           "V0301030101": 2.73}),
    "elements_embedded_electronics_std_bev_2030": dict(
        q="Elemental composition (kg) of the embedded electronics in a standard BEV, 2030.",
        multi={"Copper": 11.9, "Aluminium": 1.02, "Iron": 0.0963,
               "Silver": 0.0295, "Gold": 0.0121, "Palladium": 0.00206},
        multi_uncertainty={"Copper": 1.41, "Aluminium": 0.0937, "Iron": 0.011,
                           "Silver": 0.00305, "Gold": 0.00145, "Palladium": 0.000248}),
    # New embedded-CONTROLLERS recycling question (not in the SI; values data-derived
    # and verified on the fq endpoint — the metal-wheel/screenshot answer).
    "crm_recovered_embedded_controllers_std_bev_2025": dict(
        q="Recoverable CRM quantities in the embedded controllers of a standard BEV, 2025.",
        multi={"Copper": 0.795906, "Aluminium": 0.122864, "Palladium": 0.000432581}),
    # The SI's 10th competency question — a MEMBERSHIP answer (no number): the recovery
    # routes by which copper is recovered, keyed to each route's base-metal ChEBI class.
    # Copper's own route (CHEBI_28694, smelt/refine) + the accompanying-metal routes
    # (lead CHEBI_25016, tin CHEBI_27007, zinc CHEBI_27363).
    "cu_recovery_from_car": dict(
        q="Recovery routes by which copper is recovered from a car (membership, no number).",
        members={"smelt_refine",
                 "roast-leach_electrowinning_RLE_smeltfumerefine",
                 "smelt_fume_refine"},
        note="Cu's own route (smelt_refine) + accompanying-metal routes: Pb/Zn "
             "(roast-leach_electrowinning_RLE_smeltfumerefine), Sn (smelt_fume_refine)."),
}


def run_query(endpoint, query):
    data = urllib.parse.urlencode({"query": query}).encode()
    req = urllib.request.Request(
        endpoint, data=data,
        headers={"Accept": "application/sparql-results+json",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def fnum(binding, var):
    return float(binding[var]["value"]) if var in binding else None


def close(a, b, tol=0.02):
    return a is not None and abs(a - b) <= abs(b) * tol + 1e-6


def check(spec, rows):
    """Compare endpoint rows to the SI spec. Returns (ok, got_str). No arithmetic
    beyond the comparison — the engine already produced every number."""
    if "members" in spec:
        # MEMBERSHIP answer (no number): collect the set of URI-cell localnames the
        # query returned and compare it, as a set, to the expected member localnames.
        got = set()
        for b in rows:
            for cell in b.values():
                if cell.get("type") == "uri":
                    got.add(cell["value"].split("#")[-1].split("/")[-1])
        want = spec["members"]
        ok = want <= got                     # every expected member must be present
        return ok, f"matched {len(want & got)}/{len(want)} (got {sorted(want & got)})"
    if "multi" in spec:
        # collect {iri-localname : (kg, unc_or_None)} from rows. The key is the
        # ?key variable (the constituent/segment the row is about); ?unc is the
        # uncertainty; the first OTHER numeric is kg. Extra URI columns the newer
        # queries add (?route, ?baseMetal — the recovery route a metal travels)
        # are NOT keys: a metal recovered via several routes is several rows but
        # ONE element, so we dedupe on ?key.
        got = {}
        for b in rows:
            kc = b.get("key")
            key = (kc["value"].split("#")[-1].split("_in_")[0]
                   if kc and kc.get("type") == "uri" else None)
            if key is None:                       # fall back to the first URI cell
                for cell in b.values():
                    if cell.get("type") == "uri":
                        key = cell["value"].split("#")[-1].split("_in_")[0]
                        break
            kg = unc = None
            nums = []
            for var, cell in b.items():
                if cell.get("type") == "uri":
                    continue
                try:
                    v = float(cell["value"])
                except (ValueError, TypeError):
                    continue
                if var == "unc":
                    unc = v
                else:
                    nums.append(v)
            if nums:
                kg = nums[0]
            if key and kg is not None:
                got.setdefault(key, (kg, unc))

        def find(sub):
            for gk, gv in got.items():
                if sub in gk:
                    return gv
            return None
        exp = spec["multi"]
        exp_unc = spec.get("multi_uncertainty", {})
        hits_kg = 0
        hits_unc = 0
        for k, v in exp.items():
            row = find(k)
            kg_ok = row is not None and close(row[0], v)
            if kg_ok:
                hits_kg += 1
            if exp_unc and k in exp_unc:
                unc_ok = row is not None and close(row[1], exp_unc[k], tol=0.05)
                if unc_ok:
                    hits_unc += 1
        # EQUALITY, not subset: a result ROW the golden doesn't account for is an
        # EXTRA — the golden is incomplete (this is how a 10-vs-13 truncation slipped
        # through when we only checked "every expected key is present"). A row is
        # accounted-for iff its key contains one of the expected substrings.
        extra = [gk for gk in got
                 if not any(sub in gk for sub in exp)]
        ok = hits_kg == len(exp) and not extra
        msg = f"kg {hits_kg}/{len(exp)}"
        if exp_unc:
            ok = ok and hits_unc == len(exp_unc)
            msg += f"  unc {hits_unc}/{len(exp_unc)}"
            # report mismatches
            mismatches = []
            for k, eu in exp_unc.items():
                row = find(k)
                got_u = row[1] if row else None
                if not close(got_u, eu, tol=0.05):
                    got_s = f"{got_u:.4g}" if got_u is not None else "None"
                    mismatches.append(f"{k}={got_s}(exp {eu})")
            if mismatches:
                msg += f"  UNC MISMATCH: {mismatches[:4]}"
        msg += f" (rows={len(got)})"
        if extra:
            msg += f"  EXTRA rows not in golden: {sorted(extra)[:6]}"
        return ok, msg
    # single (+ optional uncertainty): query projects ?total (+ ?unc)
    b = rows[0] if rows else {}
    total = fnum(b, "total")
    if total is None and b:  # fall back to the first numeric column
        for var, cell in b.items():
            try:
                total = float(cell["value"]); break
            except (ValueError, TypeError):
                pass
    unc = fnum(b, "unc")
    ok = close(total, spec["value"])
    gs = f"{total:.4g}" if total is not None else "—"
    if "uncertainty" in spec:
        ok = ok and close(unc, spec["uncertainty"], tol=0.05)
        gs += f" ± {unc:.4g}" if unc is not None else " ± —"
    return ok, gs


def si_str(spec):
    if "members" in spec:
        return "{" + ", ".join(sorted(spec["members"])) + "}"
    if "multi" in spec:
        exp_unc = spec.get("multi_uncertainty", {})
        parts = []
        for k, v in spec["multi"].items():
            u = exp_unc.get(k)
            parts.append(f"{k}={v}" + (f"±{u}" if u is not None else ""))
        return "; ".join(parts)
    return f"{spec['value']}" + (f" ± {spec['uncertainty']}" if "uncertainty" in spec else "")


def main():
    md = ["# Domain query solutions — one SPARQL query per question per backend, "
          "checked vs the SI result", "",
          "Generated by `verify_domain_queries.py`. Each question is solved on BOTH "
          "backends — the **fq** `/query` precomputed view (`<group>/fq/`) and the **raw "
          "composition** statement-tree view (`<group>/composition/`), grouped into "
          "`competency/` and `paper/` to mirror the testcase split — and each "
          "computed value is compared to the SI value. The script holds **no "
          "arithmetic**: every value (RSS uncertainty via `afn:sqrt`, and the "
          "composition-side DQS→limit→σ formula) is computed entirely in SPARQL.", ""]
    overall_ok = True
    per_backend = {}
    for label, be_dir, endpoint in BACKENDS:
        print(f"\n=== {label}  ({endpoint}) ===")
        npass = 0
        n = 0
        for cid, spec in CASES.items():
            qpath = os.path.join(HERE, group_of(cid), be_dir, cid + ".rq")
            if not os.path.exists(qpath):
                print(f"[SKIP] {cid}: no query file in {group_of(cid)}/{be_dir}/")
                continue
            n += 1
            query = open(qpath).read()
            try:
                res = run_query(endpoint, query)
                ok, got = check(spec, res["results"]["bindings"])
            except Exception as e:                  # endpoint down / query error
                ok, got = False, f"ERROR: {e}"
            npass += ok
            overall_ok = overall_ok and ok
            print(f"[{'PASS' if ok else 'FAIL'}] {cid}\n        got: {got}\n        SI : {si_str(spec)}")
        per_backend[label] = (npass, n)
        print(f"  {label}: {npass}/{n}")

    # markdown: one section per question, both backends side by side
    summary = " · ".join(f"**{l}: {p}/{n}**" for l, (p, n) in per_backend.items())
    md += [summary, ""]
    for cid, spec in CASES.items():
        md.append(f"## {cid}")
        md.append("")
        md.append(f"**Question:** {spec['q']}  ")
        md.append(f"**SI:** `{si_str(spec)}`")
        if spec.get("note"):
            md.append(f"  \n> {spec['note']}")
        md.append("")
        for label, be_dir, _ in BACKENDS:
            rel = f"{group_of(cid)}/{be_dir}/{cid}.rq"
            qpath = os.path.join(HERE, rel)
            if not os.path.exists(qpath):
                continue
            md.append(f"### {label} query (`{rel}`)")
            md.append("```sparql")
            md.append(open(qpath).read().strip())
            md.append("```")
            md.append("")
        md.append("---")
        md.append("")
    print("\n" + summary.replace("**", ""))
    out = os.path.join(HERE, "DOMAIN_QUERY_SOLUTIONS.md")
    with open(out, "w") as fh:
        fh.write("\n".join(md))
    print(f"markdown -> {out}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())

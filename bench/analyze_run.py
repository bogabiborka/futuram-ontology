#!/usr/bin/env python3
"""Full-fledged error analyzer for a bench run.

Goes BEYOND the per-case `struggle_reason` (the model's own post-mortem, which is
often a plausible-but-wrong rationalisation). For every case it:

  1. CENSUSES the tool calls — search / list_skills / get_skill / find_candidate /
     execute_sparql_query — and classifies each query's RESULT (data / empty / error).
  2. RECONSTRUCTS the reasoning trace — the assistant's own text between tool calls,
     segmented into phases (discover → query → answer), with the time/tokens spent.
  3. DETECTS behavioural anti-patterns from the ACTIONS, not the prose:
       - hand-listed a VALUES / IN(...) block of subject or constituent IRIs
       - never ran a query against the core composition predicate (explored only)
       - gave up: ran N queries, emitted no ANSWER line
       - reported a kg/kg fraction to a "how many kg" question
       - dribbled: many near-identical queries
       - resolved a class but answered on a different (coarser/finer) one
       - stopped at a material/alloy when the question asked for an element
  4. SUMMARISES per case (machine verdict vs the model's struggle_reason, so you can
     see where the post-mortem and the actions disagree) and AGGREGATES across the run.

Usage:
    uv run --with rdflib python bench/analyze_run.py <run_dir> [--backend fq|composition]
                                                              [--case <substr>] [--full]
    # <run_dir> is e.g. bench/experiments/<name>/runs/<ts>
"""
from __future__ import annotations
import argparse
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

# ---- core schema vocabulary the analyzer keys off (the predicates a real DATA query
# must touch; anything else is discovery/exploration) ----------------------------
CORE_DATA_PREDICATES = (
    "fq:contains", "fq:amount", "fq:itemMass",
    "hasCompositionStatement", "hasPartRelation", "refersTo", "hasBestValue",
)
DISCOVERY_PREDICATES = ("rdfs:label", "rdfs:comment", "rdfs:subClassOf", "a owl:Class",
                        "rdf:type", "owl:Class")
KG_QUESTION = re.compile(r"\b(how many kilograms?|how much mass|kg of|total .*content|"
                         r"mass of|demand)\b", re.I)


# ----------------------------------------------------------------------------------
def classify_tool_result(content: str) -> str:
    """data | empty | error | other — from a tool result string."""
    c = content or ""
    low = c.lower()
    if "results of sparql query" in low or '"bindings"' in c:
        # a SPARQL result envelope — did it bind any rows?
        m = re.search(r'"bindings"\s*:\s*\[(.*?)\]', c, re.S)
        if m and m.group(1).strip():
            return "data"
        # bindings present but empty, or a count of 0
        return "empty"
    if "error" in low or "malformed" in low or "exception" in low or "parse" in low and "fail" in low:
        return "error"
    return "other"


def extract_queries(messages: list[dict]) -> list[dict]:
    """Ordered list of {kind, arg, result_kind, result_excerpt} for every tool call,
    pairing each assistant tool_call with the tool message that answered it."""
    steps = []
    pending = []  # tool_calls awaiting their result messages, in order
    for m in messages:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                name = tc.get("name")
                args = tc.get("arguments") or {}
                if name == "execute_sparql_query":
                    q = args.get("sparql_query") or args.get("query") or ""
                    pending.append({"kind": "query", "arg": q})
                elif name == "find_candidate_classes":
                    pending.append({"kind": "candidate", "arg": args.get("term", "")})
                elif name == "get_skill":
                    pending.append({"kind": "skill", "arg": args.get("skill_id", "")})
                elif name == "search_sparql_docs":
                    pending.append({"kind": "search", "arg": args.get("question", "")})
                elif name == "list_skills":
                    pending.append({"kind": "list_skills", "arg": ""})
                else:
                    pending.append({"kind": name or "?", "arg": str(args)[:80]})
        elif m.get("role") == "tool" and pending:
            step = pending.pop(0)
            content = m.get("content", "") or ""
            if step["kind"] == "query":
                step["result_kind"] = classify_tool_result(content)
            step["result_excerpt"] = content[:160].replace("\n", " ")
            steps.append(step)
    # any tool_calls without a paired result (truncated run)
    steps.extend(pending)
    return steps


def reasoning_segments(messages: list[dict]) -> list[str]:
    """The assistant's own reasoning text, in order (non-empty content turns)."""
    return [m["content"].strip() for m in messages
            if m.get("role") == "assistant" and (m.get("content") or "").strip()]


# ---- behavioural anti-pattern detectors (read the ACTIONS, not the post-mortem) ---
def detect_patterns(steps: list[dict], result: dict) -> list[str]:
    flags = []
    queries = [s for s in steps if s["kind"] == "query"]
    qtext = "\n".join(s["arg"] for s in queries)
    ran_core = any(any(p in s["arg"] for p in CORE_DATA_PREDICATES) for s in queries)
    any_data = any(s.get("result_kind") == "data" for s in queries)

    # 1. hand-listed a VALUES / IN(...) block of concrete IRIs (subject or constituent)
    for s in queries:
        a = s["arg"]
        vals = re.search(r"VALUES\s+\??\w*\s*\{([^}]*)\}", a)
        if vals and vals.group(1).count("#") >= 3:
            flags.append("hand-listed-VALUES")
            break
    if "hand-listed-VALUES" not in flags:
        if re.search(r"\bIN\s*\(\s*<[^)]*#[^)]*<[^)]*#", qtext) or \
           re.search(r"(=\s*<[^>]+#[^>]+>\s*\|\|){2,}", qtext):
            flags.append("hand-listed-IN/FILTER")

    # 2. explored only — never hit a core data predicate
    if queries and not ran_core:
        flags.append("never-ran-data-query")

    # 3. gave up — queries ran but no parseable answer
    if result.get("answer") is None and queries:
        flags.append("no-answer-despite-queries")

    # 4. kg/kg fraction reported to a kg question
    ans = result.get("answer") or {}
    unit = (ans.get("unit") or "") if isinstance(ans, dict) else ""
    if KG_QUESTION.search(result.get("question", "")) and "kg/kg" in unit:
        flags.append("kg-fraction-for-kg-question")

    # 5. dribble — many near-identical queries (same first 40 chars)
    sigs = Counter(s["arg"][:40] for s in queries)
    if any(v >= 4 for v in sigs.values()):
        flags.append("dribble-repeated-query")
    if len(queries) >= 25:
        flags.append("high-query-count(%d)" % len(queries))

    # 6. ran data queries but every one came back empty (wrong subject/level)
    if queries and ran_core and not any_data:
        flags.append("core-queries-all-empty(wrong-subject/level)")

    # 7. answered on a class whose level differs from the question's constituent ask
    # (heuristic: question asks an ELEMENT but the answer labels are material/alloy)
    if re.search(r"\belement|critical raw material|\bree\b|rare.earth", result.get("question",""), re.I):
        labs = ans.get("labels") if isinstance(ans, dict) else None
        if labs and any(re.search(r"Alloy|Steel|Iron|HSS|Material", str(l), re.I) for l in labs):
            flags.append("stopped-at-material-not-element")
    return flags


def analyze_case(path: pathlib.Path) -> dict:
    x = json.loads(path.read_text())
    conv = x.get("conversation", []) or []
    # flatten all attempts' messages (keep attempt boundaries for phase counting)
    all_steps, all_reasoning = [], []
    for a in conv:
        msgs = a.get("messages", [])
        all_steps += extract_queries(msgs)
        all_reasoning += reasoning_segments(msgs)
    queries = [s for s in all_steps if s["kind"] == "query"]
    census = Counter(s["kind"] for s in all_steps)
    qresults = Counter(s.get("result_kind", "?") for s in queries)
    flags = detect_patterns(all_steps, x)
    # rank the flags by how diagnostic they are, so machine_verdict is the most
    # specific cause, not whichever fired first
    SEVERITY = ["kg-fraction-for-kg-question", "stopped-at-material-not-element",
                "core-queries-all-empty(wrong-subject/level)", "hand-listed-VALUES",
                "hand-listed-IN/FILTER", "never-ran-data-query",
                "no-answer-despite-queries", "dribble-repeated-query"]
    ranked = sorted(flags, key=lambda f: next((i for i, s in enumerate(SEVERITY)
                                               if f.startswith(s.split("(")[0])), 99))
    # a correct case is "ok" no matter what habits it showed; only FAILED cases get a
    # fault verdict (the flags still record the habit for the aggregate counts)
    if x.get("correct"):
        verdict = "ok"
    else:
        verdict = ranked[0] if ranked else "unclassified"
    return {
        "case": x.get("case_id") or path.stem,
        "backend": x.get("backend"),
        "correct": x.get("correct"),
        "category": x.get("error_category"),
        "attempts": len(conv),
        "seconds": round(x.get("seconds") or 0, 1),
        "tokens": x.get("tokens_total"),
        "question": (x.get("question") or "")[:120],
        "census": dict(census),
        "query_results": dict(qresults),
        "n_queries": len(queries),
        "flags": ranked,
        "machine_verdict": verdict,
        "struggle_reason": (x.get("struggle_reason") or "")[:300],
        "final_sparql": (x.get("final_sparql") or "")[:400],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--backend", choices=["fq", "composition"], default=None)
    ap.add_argument("--case", default=None, help="substring filter on case name")
    ap.add_argument("--full", action="store_true", help="print per-case reasoning + queries")
    args = ap.parse_args()

    run = pathlib.Path(args.run_dir)
    pat = "*.json"
    files = [f for f in sorted(run.glob(pat))
             if f.name not in ("run_meta.json", "meta.json")
             and "__" in f.name]
    if args.backend:
        files = [f for f in files if f.stem.endswith("__" + args.backend)]
    if args.case:
        files = [f for f in files if args.case in f.name]
    if not files:
        print(f"no case files in {run}"); sys.exit(1)

    cases = [analyze_case(f) for f in files]

    # ---- per-case table -----------------------------------------------------------
    print(f"\n{'='*100}\nRUN: {run}\n{'='*100}")
    by_be = defaultdict(list)
    for c in cases:
        by_be[c["backend"]].append(c)
    for be, cs in sorted(by_be.items()):
        ok = sum(1 for c in cs if c["correct"])
        print(f"\n### {be}: {ok}/{len(cs)} correct\n")
        print(f"  {'case':<46} {'ok':<3} {'category':<16} {'q':>3} {'data/empty/err':<16} machine-verdict")
        for c in sorted(cs, key=lambda c: (bool(c['correct']), c['case'])):
            mark = "OK" if c["correct"] else "XX"
            qr = c["query_results"]
            qrs = f"{qr.get('data',0)}/{qr.get('empty',0)}/{qr.get('error',0)}"
            print(f"  {c['case']:<46} {mark:<3} {str(c['category'] or ''):<16} "
                  f"{c['n_queries']:>3} {qrs:<16} {c['machine_verdict']}")

    # ---- aggregate anti-pattern frequency ----------------------------------------
    print(f"\n{'='*100}\nMACHINE-DETECTED ANTI-PATTERNS (from actions, NOT struggle_reason)\n{'='*100}")
    flagc = Counter()
    fail_flagc = Counter()
    for c in cases:
        for fl in c["flags"]:
            flagc[fl] += 1
            if not c["correct"]:
                fail_flagc[fl] += 1
    for fl, n in flagc.most_common():
        print(f"  {n:>3}×  ({fail_flagc[fl]} on FAILED)  {fl}")

    # ---- where the model's POST-MORTEM disagrees with its ACTIONS -----------------
    print(f"\n{'='*100}\nPOST-MORTEM vs ACTIONS (cases where the struggle_reason omits the detected cause)\n{'='*100}")
    for c in cases:
        if c["correct"] or not c["flags"]:
            continue
        sr = c["struggle_reason"].lower()
        verdict = c["machine_verdict"]
        # crude: does the post-mortem mention the machine-detected issue?
        kw = {"hand-listed-VALUES": ["values", "hard-cod", "enumerat", "hand"],
              "never-ran-data-query": ["never", "did not run", "explor", "no data query"],
              "kg-fraction-for-kg-question": ["fraction", "itemmass", "absolute", "multiply"],
              "stopped-at-material-not-element": ["deeper", "element", "level", "travers"],
              "core-queries-all-empty(wrong-subject/level)": ["wrong", "level", "class"],
              }.get(verdict, [])
        mentioned = any(k in sr for k in kw) if kw else None
        if mentioned is False:
            print(f"  ⚠ {c['case']}: actions say [{verdict}] but post-mortem doesn't — "
                  f"post-mortem blames: \"{c['struggle_reason'][:110]}…\"")

    # ---- optional full per-case dump ---------------------------------------------
    if args.full:
        for c in cases:
            if c["correct"]:
                continue
            print(f"\n{'-'*100}\n{c['case']}  [{c['backend']}]  cat={c['category']}  "
                  f"verdict={c['machine_verdict']}\n  Q: {c['question']}")
            print(f"  tool census: {c['census']}   query results: {c['query_results']}")
            print(f"  flags: {c['flags']}")
            print(f"  final SPARQL: {c['final_sparql'][:200]}")
            print(f"  model post-mortem: {c['struggle_reason'][:200]}")


if __name__ == "__main__":
    main()

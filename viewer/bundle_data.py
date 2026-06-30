#!/usr/bin/env python3
"""
Bundle one bench experiment into viewer/public/data.json for the static viewer.

Usage:
    python viewer/bundle_data.py           # the published default experiment
    python viewer/bundle_data.py nightly-2 # specific experiment

Run from the repo root.
"""

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).parent.parent
EXPERIMENTS_DIR = REPO / "bench" / "experiments"
OUT_DIR = REPO / "viewer" / "public"
OUT_FILE = OUT_DIR / "data.json"

# The experiment published to GitHub Pages when no name is given (the deploy
# workflow calls this script with an empty argument). Override with an explicit
# name on the CLI or via the workflow_dispatch input.
DEFAULT_EXPERIMENT = "all-in-1"


def latest_run(exp_dir: pathlib.Path) -> pathlib.Path | None:
    runs = sorted((exp_dir / "runs").iterdir()) if (exp_dir / "runs").exists() else []
    return runs[-1] if runs else None


def pick_experiment(name: str | None) -> pathlib.Path:
    name = name or DEFAULT_EXPERIMENT
    p = EXPERIMENTS_DIR / name
    if not p.exists():
        sys.exit(f"Experiment not found: {name}")
    return p


def load_case(f: pathlib.Path) -> dict:
    d = json.loads(f.read_text())
    # Keep the full conversation for the reader view.
    # Also derive a lean timeline for the step-pills summary.
    conv = d.get("conversation", [])
    d["timeline"] = extract_timeline(conv)
    return d


def extract_timeline(conversation: list) -> list:
    """Derive a lean step list (for timeline pills) from the full conversation."""
    import re
    attempts = []
    for entry in conversation:
        msgs = entry.get("messages", [])
        attempt_num = entry.get("attempt", len(attempts) + 1)
        steps = []
        for m in msgs:
            if m["role"] == "assistant":
                for tc in m.get("tool_calls") or []:
                    name = tc.get("name", "")
                    args = tc.get("arguments", {})
                    if name == "execute_sparql_query":
                        steps.append({"type": "query", "query": args.get("sparql_query", "")})
                    elif name == "search_sparql_docs":
                        steps.append({"type": "search"})
                    elif name == "list_skills":
                        steps.append({"type": "skills"})
                    elif name == "get_skill":
                        steps.append({"type": "skill", "skill": args.get("skill_id", "")})
                if "ANSWER:" in (m.get("content") or ""):
                    steps.append({"type": "answer", "text": m["content"].strip()})
            elif m["role"] == "tool":
                if steps:
                    last = steps[-1]
                    if last.get("type") in ("query", "search", "skills", "skill") and "result" not in last:
                        content = m.get("content") or ""
                        last["result"] = content[:800]
                        last["kind"] = classify_result(content)
            elif m["role"] == "user":
                content = m.get("content") or ""
                m2 = re.search(r'\[Retry[^\]]*\]\s*(.*)', content, re.DOTALL)
                if m2:
                    steps.append({"type": "reprompt", "reason": m2.group(1).split("\n")[0].strip()})
        attempts.append({"attempt": attempt_num, "steps": steps})
    return attempts


def classify_result(content: str) -> str:
    c = content.strip()
    if not c:
        return "empty"
    if c.startswith("[error]") or "ParseException" in c or "MalformedQuery" in c or c.startswith("Error"):
        return "invalid"
    if c in ("[]", "No results") or c.lower().startswith("no results"):
        return "empty"
    return "data"


def load_run(run_dir: pathlib.Path) -> dict:
    environment = None
    results_path = run_dir / "results.json"
    if results_path.exists():
        try:
            rd = json.loads(results_path.read_text())
            environment = rd.get("environment")
        except Exception:
            pass

    # Per-case transcript files, keyed (case_id, backend). Scoreboard files
    # ({environment, results}, incl. the per-rerun ones) and the run's meta.json
    # are not transcripts.
    cases = {}
    for f in sorted(run_dir.glob("*.json")):
        if f.name.startswith("results") or f.name == "meta.json":
            continue
        try:
            c = load_case(f)
            cases[(c.get("case_id"), c.get("backend"))] = c
        except Exception as e:
            print(f"  Warning: skipping {f.name}: {e}")

    # A re-run of a (case, backend) is the LATEST result for that pair and wins:
    # overlay the rerun's verdict fields onto the case transcript. (results_rerun_*
    # files are written when a single case is re-run after the main pass.)
    VERDICT_FIELDS = ("correct", "got", "expected", "score_detail", "error_category",
                      "answer_raw", "final_text", "final_sparql", "struggle_reason",
                      "provider_error", "error")
    for f in sorted(run_dir.glob("results_rerun_*.json")):
        try:
            for r in json.loads(f.read_text()).get("results", []):
                key = (r.get("case_id"), r.get("backend"))
                if key in cases:
                    for fld in VERDICT_FIELDS:
                        if fld in r:
                            cases[key][fld] = r[fld]
        except Exception as e:
            print(f"  Warning: skipping rerun {f.name}: {e}")

    return {"ts": run_dir.name, "environment": environment,
            "cases": list(cases.values())}


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else None
    exp_dir = pick_experiment(name)
    print(f"Experiment: {exp_dir.name}")

    config = json.loads((exp_dir / "experiment.json").read_text())
    config["name"] = exp_dir.name

    run_dir = latest_run(exp_dir)
    print(f"Run: {run_dir.name}")
    run = load_run(run_dir)
    print(f"Cases loaded: {len(run['cases'])}")

    payload = {"config": config, "run": run}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(payload, separators=(",", ":")))
    size_kb = OUT_FILE.stat().st_size / 1024
    print(f"Wrote {OUT_FILE} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()

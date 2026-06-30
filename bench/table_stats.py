#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml", "requests"]
# ///
"""Print Table 1 (answer quality), Table 2 (query effort), and Table 3 (dataset
scale & query runtime) from bench runs.

Usage
-----
  # Latest run of a named experiment:
  uv run bench/table_stats.py --experiment with-skills

  # Specific run (timestamp):
  uv run bench/table_stats.py --experiment with-skills/2026-06-26T11-16-25-299Z

  # Compare two experiments side-by-side (each gets its own column group):
  uv run bench/table_stats.py --experiment with-skills --experiment without-skills

  # Raw directory of per-case JSONs (bench/live or an experiment run dir):
  uv run bench/table_stats.py --dir bench/live/

  # Single --json dump from run_bench.py --json:
  uv run bench/table_stats.py --json results.json

  # Filter to specific backends:
  uv run bench/table_stats.py --experiment with-skills --backends fq,composition

  # Filter to specific cases (substring match on case_id):
  uv run bench/table_stats.py --experiment with-skills --cases al_diesel,cu_hev

  # Exclude __valunc twin cases (keep only base questions):
  uv run bench/table_stats.py --experiment with-skills --no-valunc

  # Also emit Table 3 (dataset scale) — queries the live Fuseki endpoints:
  uv run bench/table_stats.py --experiment all-in-1 --endpoints

  # Override the default endpoint URLs for Table 3:
  uv run bench/table_stats.py --experiment all-in-1 --endpoints \\
      --fq-url http://localhost:47040/query/sparql \\
      --composition-url http://localhost:47040/composition/sparql

  # List available experiments:
  uv run bench/table_stats.py --list

Output: tables printed to stdout, ready to paste into a spreadsheet/paper.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


EXPERIMENTS_DIR = Path(__file__).parent / "experiments"

DEFAULT_FQ_URL = "http://localhost:47040/query/sparql"
DEFAULT_COMPOSITION_URL = "http://localhost:47040/composition/sparql"


# ── experiment resolution ─────────────────────────────────────────────────────

def _resolve_experiment(spec: str) -> tuple[str, Path]:
    """Given 'name' or 'name/timestamp', return (label, run_dir).
    'name' alone picks the latest run."""
    parts = spec.split("/", 1)
    name = parts[0]
    exp_dir = EXPERIMENTS_DIR / name
    if not exp_dir.is_dir():
        raise SystemExit(f"experiment {name!r} not found in {EXPERIMENTS_DIR}")
    runs_dir = exp_dir / "runs"
    if len(parts) == 2:
        run_dir = runs_dir / parts[1]
        if not run_dir.is_dir():
            raise SystemExit(f"run {parts[1]!r} not found under {runs_dir}")
        label = f"{name}/{parts[1]}"
    else:
        candidates = sorted(runs_dir.iterdir()) if runs_dir.is_dir() else []
        candidates = [c for c in candidates if c.is_dir()]
        if not candidates:
            raise SystemExit(f"no runs found for experiment {name!r}")
        run_dir = candidates[-1]     # latest by lexicographic sort (ISO timestamp)
        label = f"{name}/{run_dir.name}"
    return label, run_dir


def _list_experiments() -> None:
    if not EXPERIMENTS_DIR.is_dir():
        print("No experiments directory found.")
        return
    for exp in sorted(EXPERIMENTS_DIR.iterdir()):
        if not exp.is_dir():
            continue
        runs_dir = exp / "runs"
        runs = sorted(runs_dir.iterdir()) if runs_dir.is_dir() else []
        runs = [r for r in runs if r.is_dir()]
        cfg_path = exp / "experiment.json"
        model = ""
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text())
                model = cfg.get("model", "")
            except Exception:
                pass
        print(f"  {exp.name:<30}  {len(runs)} run(s)  model={model}")
        for r in runs[-3:]:     # show last 3 runs
            n = len(list(r.glob("*.json")))
            print(f"    {r.name}  ({n} result files)")


# ── loading ───────────────────────────────────────────────────────────────────

_SKIP_FILES = {"meta.json", "experiment.json", "results.json"}

def _load_dir(path: Path) -> list[dict]:
    results = []
    for f in sorted(path.glob("*.json")):
        if f.name in _SKIP_FILES:
            continue
        try:
            d = json.loads(f.read_text())
            if d.get("status") == "running":
                continue
            # skip aggregate/envelope files (no backend field)
            if d.get("backend") is None and "results" in d:
                continue
            results.append(d)
        except Exception:
            pass
    return results


def _load_json_file(path: Path) -> list[dict]:
    d = json.loads(path.read_text())
    if isinstance(d, dict) and "results" in d:
        return d["results"]
    if isinstance(d, list):
        return d
    return []


def _filter(results: list[dict], backends: list[str] | None,
            cases: list[str] | None, no_valunc: bool) -> list[dict]:
    if backends:
        results = [r for r in results if r.get("backend") in backends]
    if cases:
        results = [r for r in results if any(c in (r.get("case_id") or "") for c in cases)]
    if no_valunc:
        results = [r for r in results if "__valunc" not in (r.get("case_id") or "")]
    return results


# ── formatting helpers ────────────────────────────────────────────────────────

def _avg(xs):
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None


def _fmt(v, fmt=".1f"):
    return f"{v:{fmt}}" if v is not None else "—"


def _pct(n, total):
    return f"{100 * n / total:.0f}%" if total else "—"


ERROR_SHORT = {
    "wrong-class": "cls", "wrong-value": "val",
    "wrong-uncertainty": "unc", "no-answer": "none",
    "not-grounded": "ungrd", "wrong-shape": "shape",
    "timeout": "time!", "token-cap": "tok!",
    "provider-error": "prov", "other": "other",
}

ALL_CATS = [
    "wrong-class", "wrong-value", "wrong-uncertainty",
    "no-answer", "not-grounded", "wrong-shape",
    "timeout", "token-cap", "provider-error", "other",
]


# ── columns: one per (experiment, backend) ────────────────────────────────────

def _make_columns(runs: list[tuple[str, list[dict]]], backends: list[str] | None
                  ) -> list[tuple[str, list[dict]]]:
    """Return [(col_label, [results]), ...] — one column per (exp_label, backend)."""
    cols = []
    for exp_label, results in runs:
        found_backends = list(dict.fromkeys(r.get("backend") for r in results))
        be_order = (backends or
                    sorted(found_backends, key=lambda b: (b != "fq", b != "composition", b)))
        for be in be_order:
            rs = [r for r in results if r.get("backend") == be]
            if rs:
                label = f"{exp_label}\n({be})" if len(runs) > 1 else be
                cols.append((label, rs))
    return cols


# ── Table 1 ───────────────────────────────────────────────────────────────────

def table1(cols: list[tuple[str, list[dict]]]) -> None:
    col_w = 38
    hdr_labels = [lbl.replace("\n", " / ") for lbl, _ in cols]
    cell_w = max(16, max(len(h) for h in hdr_labels) + 2)

    def _row(label, vals):
        print(f"{label:<{col_w}}" + "".join(f"  {v:>{cell_w}}" for v in vals))

    print("=" * (col_w + (cell_w + 2) * len(cols)))
    print("TABLE 1 — Answer quality")
    print("=" * (col_w + (cell_w + 2) * len(cols)))
    hdr = f"{'Metric':<{col_w}}" + "".join(f"  {h:>{cell_w}}" for h in hdr_labels)
    print(hdr)
    print("-" * len(hdr))

    def _grounded(r):
        return (r.get("answer") is not None
                and r.get("error_category") not in
                ("not-grounded", "provider-error", "timeout", "token-cap"))

    ns = [len(rs) for _, rs in cols]
    oks = [sum(1 for r in rs if r.get("correct")) for _, rs in cols]
    grs = [sum(1 for r in rs if _grounded(r)) for _, rs in cols]

    _row("N questions", [str(n) for n in ns])
    _row("Correct answers",
         [f"{ok}/{n} ({_pct(ok, n)})" for ok, n in zip(oks, ns)])
    _row("Grounded answers (SPARQL-traced)",
         [f"{gr}/{n} ({_pct(gr, n)})" for gr, n in zip(grs, ns)])

    # uncertainty — only __valunc cases
    unc_rs = [[r for r in rs if "__valunc" in (r.get("case_id") or "")] for _, rs in cols]
    if any(unc_rs):
        unc_ok = [sum(1 for r in rs if r.get("correct")) for rs in unc_rs]
        unc_n = [len(rs) for rs in unc_rs]
        _row("Uncertainty correct (±≤20% SI)",
             [f"{ok}/{n} ({_pct(ok, n)})" if n else "—"
              for ok, n in zip(unc_ok, unc_n)])

    print()
    print(f"{'Error breakdown (failures only)':<{col_w}}")

    cats_per = [Counter(r.get("error_category") for r in rs
                        if not r.get("correct") and r.get("error_category"))
                for _, rs in cols]
    for cat in ALL_CATS:
        vals = [cats_per[i].get(cat, 0) for i in range(len(cols))]
        if any(v > 0 for v in vals):
            _row(f"  {cat}", [str(v) if v else "—" for v in vals])

    print()


# ── Table 2 ───────────────────────────────────────────────────────────────────

def table2(cols: list[tuple[str, list[dict]]]) -> None:
    col_w = 38
    hdr_labels = [lbl.replace("\n", " / ") for lbl, _ in cols]
    cell_w = max(14, max(len(h) for h in hdr_labels) + 2)

    def _row(label, vals):
        print(f"{label:<{col_w}}" + "".join(f"  {v:>{cell_w}}" for v in vals))

    def _kpi(rs, key):
        return [r["kpis"][key] for r in rs if r.get("kpis") and key in r["kpis"]]

    print("=" * (col_w + (cell_w + 2) * len(cols)))
    print("TABLE 2 — Query effort")
    print("=" * (col_w + (cell_w + 2) * len(cols)))
    hdr = f"{'Metric':<{col_w}}" + "".join(f"  {h:>{cell_w}}" for h in hdr_labels)
    print(hdr)
    print("-" * len(hdr))

    _row("Avg SPARQL queries / question",
         [_fmt(_avg(_kpi(rs, "queries_to_answer"))) for _, rs in cols])
    _row("Avg wasted query ratio",
         [_fmt(_avg(_kpi(rs, "wrong_query_ratio")), ".2f") for _, rs in cols])
    _row("Avg LLM thinking time (s)",
         [_fmt(_avg(_kpi(rs, "llm_seconds"))) for _, rs in cols])
    _row("Avg total tokens / question",
         [_fmt(_avg(_kpi(rs, "tokens_total")), ".0f") for _, rs in cols])
    _row("Avg corrective re-prompts",
         [_fmt(_avg([r.get("subject_retries") or 0 for r in rs]))
          for _, rs in cols])
    _row("Avg wall-clock time (s)",
         [_fmt(_avg(_kpi(rs, "wall_seconds"))) for _, rs in cols])

    print()

    # per-case breakdown
    all_cases = list(dict.fromkeys(
        r.get("case_id") for _, rs in cols for r in rs))

    sep = "-" * (col_w + (cell_w + 2) * len(cols))
    print(f"{'Per-case':<{col_w}}" + "".join(f"  {h:>{cell_w}}" for h in hdr_labels))
    print(sep)

    # index each col by case_id for fast lookup
    by_case_col = []
    for _, rs in cols:
        idx = {r.get("case_id"): r for r in rs}
        by_case_col.append(idx)

    for case_id in sorted(all_cases):
        cells = []
        for idx in by_case_col:
            r = idx.get(case_id)
            if r is None:
                cells.append("—")
            elif r.get("correct"):
                cells.append("✓")
            else:
                cat = r.get("error_category") or "?"
                cells.append(f"✗ {ERROR_SHORT.get(cat, cat[:6])}")
        print(f"{case_id:<{col_w}}" + "".join(f"  {c:>{cell_w}}" for c in cells))

    print()


# ── Table 3 — dataset scale & query runtime ────────────────────────────────────

def _sparql_count(url: str, query: str, timeout: int = 10) -> int | None:
    """Run a SELECT COUNT(*) SPARQL query; return the integer count or None on error."""
    if not _HAS_REQUESTS:
        return None
    try:
        r = _requests.get(url, params={"query": query},
                          headers={"Accept": "application/sparql-results+json"},
                          timeout=timeout)
        r.raise_for_status()
        bindings = r.json()["results"]["bindings"]
        return int(bindings[0][list(bindings[0])[0]]["value"]) if bindings else 0
    except Exception:
        return None


def _sparql_timed(url: str, query: str, n_reps: int = 10, timeout: int = 30) -> dict | None:
    """Warm + time n_reps executions of a SPARQL query; return {median_ms, min_ms, max_ms}."""
    if not _HAS_REQUESTS:
        return None
    try:
        # one warm-up run
        _requests.get(url, params={"query": query},
                      headers={"Accept": "application/sparql-results+json"},
                      timeout=timeout)
        times_ms = []
        for _ in range(n_reps):
            t0 = time.perf_counter()
            r = _requests.get(url, params={"query": query},
                              headers={"Accept": "application/sparql-results+json"},
                              timeout=timeout)
            r.raise_for_status()
            times_ms.append((time.perf_counter() - t0) * 1000)
        return {
            "median_ms": round(median(times_ms), 1),
            "min_ms": round(min(times_ms), 1),
            "max_ms": round(max(times_ms), 1),
        }
    except Exception:
        return None


# Representative single-hop lookup used for response-time measurement.
# fq: read the copper content of the standard BEV class — exactly the query the LLM produces.
_FQ_TIMING_QUERY = """
PREFIX fq: <https://www.purl.org/futuram/query#>
PREFIX fut: <https://www.purl.org/futuram#>
SELECT ?amount ?itemMass WHERE {
  fut:elvBEV fq:contains ?a .
  ?a fq:constituent fut:Copper ; fq:amount ?amount .
  fut:elvBEV fq:itemMass ?itemMass .
}"""

# composition: the equivalent traversal — instances → statement → part → quantity
_COMP_TIMING_QUERY = """
PREFIX fut: <https://www.purl.org/futuram#>
PREFIX qudt: <http://qudt.org/schema/qudt/>
SELECT ?val ?unit WHERE {
  ?inst a fut:elvBEV .
  ?inst fut:hasCompositionStatement ?stmt .
  ?stmt fut:hasPartRelation ?pr .
  ?pr fut:refersTo fut:Copper .
  ?pr fut:hasBestValue ?qv .
  ?qv qudt:numericValue ?val ; qudt:unit ?unit .
} LIMIT 50"""

# Count queries used for Table 3 header row
_COUNT_TRIPLES = "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"
_COUNT_CLASSES = """SELECT (COUNT(DISTINCT ?c) AS ?n) WHERE {
  { ?c a <http://www.w3.org/2002/07/owl#Class> }
  UNION { ?s a ?c . FILTER(isIRI(?c)) }
}"""
_COUNT_INSTANCES = "SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a ?t . FILTER(isIRI(?t)) }"
_COUNT_STATEMENTS = """PREFIX fut: <https://www.purl.org/futuram#>
SELECT (COUNT(*) AS ?n) WHERE { ?s a fut:CompositionStatement }"""
_COUNT_AMOUNT_NODES = """PREFIX fq: <https://www.purl.org/futuram/query#>
SELECT (COUNT(DISTINCT ?a) AS ?n) WHERE { ?a a fq:Amount }"""
_COUNT_PRODUCTS = """PREFIX fut: <https://www.purl.org/futuram#>
SELECT (COUNT(DISTINCT ?p) AS ?n) WHERE { ?p a fut:Product }"""


def table3(fq_url: str, composition_url: str, n_reps: int = 10) -> None:
    col_w = 44
    cell_w = 20
    sep = "=" * (col_w + (cell_w + 2) * 2)

    def _row(label, fq_val, comp_val=""):
        print(f"{label:<{col_w}}  {str(fq_val):>{cell_w}}  {str(comp_val):>{cell_w}}")

    print(sep)
    print("TABLE 3 — Dataset scale & query runtime")
    print(sep)
    print(f"{'Metric':<{col_w}}  {'fq: (query-optimised)':>{cell_w}}  {'composition (raw)':>{cell_w}}")
    print("-" * (col_w + (cell_w + 2) * 2))

    if not _HAS_REQUESTS:
        print("  (install `requests` to query live endpoints: uv add requests)")
        return

    # --- scale ---
    fq_triples = _sparql_count(fq_url, _COUNT_TRIPLES)
    comp_triples = _sparql_count(composition_url, _COUNT_TRIPLES)
    fq_amounts = _sparql_count(fq_url, _COUNT_AMOUNT_NODES)
    comp_stmts = _sparql_count(composition_url, _COUNT_STATEMENTS)
    comp_products = _sparql_count(composition_url, _COUNT_PRODUCTS)

    _row("Total RDF triples",
         f"{fq_triples:,}" if fq_triples is not None else "—",
         f"{comp_triples:,}" if comp_triples is not None else "—")
    _row("fq:Amount nodes (class×constituent pairs)",
         f"{fq_amounts:,}" if fq_amounts is not None else "—", "—")
    _row("CompositionStatement nodes",
         "—",
         f"{comp_stmts:,}" if comp_stmts is not None else "—")
    _row("Product instances (all drivetrains × years)",
         "—",
         f"{comp_products:,}" if comp_products is not None else "—")

    print()

    # --- timing ---
    print(f"{'Query timing ({n} reps, after 1 warm-up)'.format(n=n_reps):<{col_w}}")
    fq_t = _sparql_timed(fq_url, _FQ_TIMING_QUERY, n_reps)
    comp_t = _sparql_timed(composition_url, _COMP_TIMING_QUERY, n_reps)

    def _t(d, key):
        return f"{d[key]:.1f} ms" if d else "—"

    _row("  Median response time (representative lookup)",
         _t(fq_t, "median_ms"), _t(comp_t, "median_ms"))
    _row("  Min response time",
         _t(fq_t, "min_ms"), _t(comp_t, "min_ms"))
    _row("  Max response time",
         _t(fq_t, "max_ms"), _t(comp_t, "max_ms"))

    print()
    print(f"  fq endpoint:           {fq_url}")
    print(f"  composition endpoint:  {composition_url}")
    print(f"  Triplestore:           Apache Jena Fuseki (TDB2)")
    print()


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true",
                   help="list available experiments and exit")
    p.add_argument("--experiment", "-e", metavar="NAME[/TIMESTAMP]",
                   action="append", dest="experiments", default=[],
                   help="experiment name (optionally with run timestamp); "
                        "repeat for side-by-side comparison")
    p.add_argument("--dir", metavar="PATH",
                   help="raw directory of per-case result JSONs")
    p.add_argument("--json", metavar="PATH",
                   help="single results JSON file (run_bench --json output)")
    p.add_argument("--backends", metavar="BE1,BE2",
                   help="comma-separated backend IDs to include (default: all found)")
    p.add_argument("--cases", metavar="STR",
                   help="comma-separated substrings to filter case IDs")
    p.add_argument("--no-valunc", action="store_true",
                   help="exclude __valunc twin cases (keep only base questions)")
    p.add_argument("--endpoints", action="store_true",
                   help="also emit Table 3 (dataset scale + query timing) by querying "
                        "the live Fuseki endpoints; requires `requests`")
    p.add_argument("--fq-url", metavar="URL", default=DEFAULT_FQ_URL,
                   help=f"fq: endpoint URL for Table 3 (default: {DEFAULT_FQ_URL})")
    p.add_argument("--composition-url", metavar="URL", default=DEFAULT_COMPOSITION_URL,
                   help=f"composition endpoint URL for Table 3 "
                        f"(default: {DEFAULT_COMPOSITION_URL})")
    p.add_argument("--timing-reps", metavar="N", type=int, default=10,
                   help="number of timed repetitions for Table 3 query timing (default: 10)")
    args = p.parse_args()

    if args.list:
        _list_experiments()
        return

    backends = [b.strip() for b in args.backends.split(",")] if args.backends else None
    cases = [c.strip() for c in args.cases.split(",")] if args.cases else None

    runs: list[tuple[str, list[dict]]] = []

    for spec in args.experiments:
        label, run_dir = _resolve_experiment(spec)
        results = _load_dir(run_dir)
        results = _filter(results, backends, cases, args.no_valunc)
        if not results:
            print(f"Warning: no results after filtering for {label!r}", file=sys.stderr)
            continue
        runs.append((label, results))

    if args.dir:
        results = _load_dir(Path(args.dir))
        results = _filter(results, backends, cases, args.no_valunc)
        runs.append((args.dir, results))

    if args.json:
        results = _load_json_file(Path(args.json))
        results = _filter(results, backends, cases, args.no_valunc)
        runs.append((args.json, results))

    if not runs and not args.endpoints:
        p.error("specify at least one of --experiment, --dir, or --json  "
                "(use --list to see available experiments)")

    if runs:
        cols = _make_columns(runs, backends)
        if not cols:
            print("No data after filtering.", file=sys.stderr)
            sys.exit(1)
        table1(cols)
        table2(cols)

    if args.endpoints:
        table3(args.fq_url, args.composition_url, args.timing_reps)


if __name__ == "__main__":
    main()

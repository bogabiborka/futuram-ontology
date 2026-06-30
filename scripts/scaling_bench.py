#!/usr/bin/env -S uv run --quiet --with rdflib --with pyyaml --with pandas --with openpyxl python
# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "pandas", "openpyxl"]
# ///
# NOTE: the derive (build_instances -> etl) needs pyyaml/pandas/openpyxl, not
# just rdflib (else every build point fails with ModuleNotFoundError).
"""scaling_bench — scaling benchmark harness for the FutuRaM composition KG.

Measures how the system scales as the dataset grows along the DRIVETRAIN axis
(reviewer Q2). The scaling axis is a sequence of CUMULATIVE drivetrain selections
via the --files knob of build_instances.py: [BEV] -> [BEV,Petrol] -> ... -> all 6.

For each point it:
  1. derives the served fq: graph via build_instances.build_futuram(files=...),
     timing the DERIVE wall-clock and capturing peak RSS;
  2. counts (rdflib) CompositionStatement individuals + per-level subclass counts
     (Product/Component/Material/Element) and fq:/composition triple counts;
  3. OPTIONALLY (only with --endpoint) times a fixed query set against a running
     Fuseki, fq: vs. a raw-composition baseline (--baseline-endpoint);
  4. writes one CSV row per point to --out (+ a sidecar env JSON).

WARNINGS / HONESTY
  * The derive is DESTRUCTIVE (rewrites the committed composition + query TTLs); pass --yes to build, --dry-run to print the plan.
  * Derive wall-clock is expected SUPER-LINEAR in N (pooled re-aggregation) — a real, reported property.
  * Query timing is OFF by default (no running server assumed).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import pathlib
import platform
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Paths + sys.path wiring (mirror pyproject pythonpath=["src","tests"]).
# ---------------------------------------------------------------------------
ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TESTS = ROOT / "tests"
for p in (SRC, TESTS):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

COMP_DIR = ROOT / "fuseki" / "futuram" / "data" / "composition"
QUERY_TTL = ROOT / "fuseki" / "futuram" / "data" / "query" / "futuram.ttl"

# Drivetrain tokens, in the cumulative order used to grow the dataset. These are
# the --files tokens accepted by build_instances.build_futuram.
DRIVETRAIN_ORDER = ["BEV", "Petrol", "Diesel", "HEV", "PHEV", "others"]

# Default cumulative sizes (number of leading drivetrains): Case 1 (BEV) ... Case 2
# (all 6), with two intermediate points so the curve is more than two dots.
DEFAULT_POINTS = [1, 2, 3, 6]

# Default fixed year slices — pinned so points differ ONLY by the drivetrain axis.
DEFAULT_YEARS = [2010, 2020, 2025, 2030, 2050]

DEFAULT_OUT = ROOT / "revision" / "scaling-results.csv"
ENV_SIDECAR = ROOT / "revision" / "scaling-env.json"

CSV_COLUMNS = [
    "point_id",
    "drivetrains",
    "n_products",
    "n_components",
    "n_materials",
    "n_elements",
    "n_statements",
    "composition_triples",
    "fq_triples",
    "fq_comp_ratio",
    "derive_seconds",
    "peak_rss_mb",
    "q_fq_median_ms",
    "q_fq_iqr_ms",
    "q_baseline_median_ms",
    "q_baseline_iqr_ms",
]

# ---------------------------------------------------------------------------
# Fixed query set (KPI #1: query-optimized fq: vs. baseline composition). The fq:
# queries below run against the SERVED endpoint; the baseline_* equivalents run
# the same question against the baseline composition endpoint (--baseline-endpoint).
# ---------------------------------------------------------------------------
FQ_PREFIXES = """
PREFIX fq: <https://www.purl.org/futuram/query#>
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""

# (a) element-in-class: how much of each constituent does a fixed class contain?
QUERY_FQ_ELEMENT_IN_CLASS = FQ_PREFIXES + """
SELECT ?constituent ?amt ?unit WHERE {
  ?cls fq:contains ?a .
  ?a fq:constituent ?constituent ;
     fq:amount ?amt ;
     fq:unit ?unit ;
     fq:whole ?cls .
  ?cls rdfs:subClassOf futuram:Component .
}
LIMIT 1000
"""

# (b) constituent-first: which class-amounts have a given whole? (fq:whole traversal)
QUERY_FQ_CONSTITUENT_FIRST = FQ_PREFIXES + """
SELECT ?whole ?constituent ?amt WHERE {
  ?a fq:whole ?whole ;
     fq:constituent ?constituent ;
     fq:amount ?amt .
  ?a fq:constituent futuram:Copper .
}
LIMIT 1000
"""

# (c) aggregate rollup: follow fq:sliceOf from leaf slice classes to their parent.
QUERY_FQ_AGGREGATE_ROLLUP = FQ_PREFIXES + """
SELECT ?parent (COUNT(?slice) AS ?nslices) WHERE {
  ?slice fq:sliceOf ?parent ;
         fq:sliceAxis futuram:DrivetrainMeanStrategy .
}
GROUP BY ?parent
LIMIT 1000
"""

QUERIES_FQ = {
    "element_in_class": QUERY_FQ_ELEMENT_IN_CLASS,
    "constituent_first": QUERY_FQ_CONSTITUENT_FIRST,
    "aggregate_rollup": QUERY_FQ_AGGREGATE_ROLLUP,
}

# Baseline: the SAME questions over the baseline composition dataset (the multi-hop
# CompositionStatement / PartRelation shape the fq: projection flattens). Run only
# if --baseline-endpoint is given; this is the contrast that justifies the query-optimized fq: dataset.
COMP_PREFIXES = """
PREFIX futuram: <https://www.purl.org/futuram#>
PREFIX ceonp: <http://w3id.org/CEON/ontology/product/>
"""

QUERY_BASELINE_ELEMENT_IN_CLASS = COMP_PREFIXES + """
SELECT ?whole ?part ?best WHERE {
  ?stmt a futuram:CompositionStatement ;
        ceonp:compositionOf ?whole ;
        futuram:hasPartRelation ?pr .
  ?pr futuram:refersTo ?part .
  OPTIONAL { ?pr futuram:best ?best }
}
LIMIT 1000
"""

QUERY_BASELINE_CONSTITUENT_FIRST = COMP_PREFIXES + """
SELECT ?whole ?pr ?best WHERE {
  ?stmt a futuram:CompositionStatement ;
        ceonp:compositionOf ?whole ;
        futuram:hasPartRelation ?pr .
  ?pr futuram:refersTo futuram:Copper .
  OPTIONAL { ?pr futuram:best ?best }
}
LIMIT 1000
"""

QUERY_BASELINE_AGGREGATE_ROLLUP = COMP_PREFIXES + """
SELECT ?whole (COUNT(?pr) AS ?nparts) WHERE {
  ?stmt a futuram:CompositionStatement ;
        ceonp:compositionOf ?whole ;
        futuram:hasPartRelation ?pr .
}
GROUP BY ?whole
LIMIT 1000
"""

QUERIES_BASELINE = {
    "element_in_class": QUERY_BASELINE_ELEMENT_IN_CLASS,
    "constituent_first": QUERY_BASELINE_CONSTITUENT_FIRST,
    "aggregate_rollup": QUERY_BASELINE_AGGREGATE_ROLLUP,
}


# ---------------------------------------------------------------------------
# Peak RSS (KPI #2). memcap._current_rss_bytes() returns ru_maxrss raw: BYTES on
# macOS, KILOBYTES on Linux. Convert to MB accordingly.
# ---------------------------------------------------------------------------
def _peak_rss_mb() -> float:
    try:
        from common.memcap import _current_rss_bytes
    except Exception:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        raw_bytes = ru if sys.platform == "darwin" else ru * 1024
        return raw_bytes / (1024.0 ** 2)
    # _current_rss_bytes already normalizes to bytes on both platforms.
    return _current_rss_bytes() / (1024.0 ** 2)


# ---------------------------------------------------------------------------
# RDF counting (KPI x-axis + #4).
# Triple counts always come from live Fuseki (after reload); level counts
# (n_products / n_components / …) still use rdflib since Fuseki doesn't track
# those per-dataset-point and the subClassOf graph is the same as what we parse.
# ---------------------------------------------------------------------------
FUT = "https://www.purl.org/futuram#"

DOCKER_COMPOSE_SERVICE = "bench-fuseki"


def _sparql_count(endpoint: str, query: str, timeout: float = 120.0) -> int:
    """Run a SELECT (COUNT(*) AS ?n) query; return the integer result."""
    data = urllib.parse.urlencode({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=data,
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        import json as _json
        body = _json.loads(resp.read())
    return int(body["results"]["bindings"][0]["n"]["value"])


def _reload_fuseki_and_count(endpoint: str, baseline_endpoint: str | None) -> dict:
    """Reload Fuseki with RELOAD=1 so it picks up freshly derived TTLs, wait for
    it to become healthy, then query both endpoints for total triple counts.
    Returns {composition_triples, fq_triples}."""
    import subprocess

    print(f"  [reload] recreating {DOCKER_COMPOSE_SERVICE} with RELOAD=1 ...",
          file=sys.stderr)
    env = {**os.environ, "RELOAD": "1"}
    compose_file = ROOT / "docker-compose.yml"
    # `restart` reuses the existing env; `up --force-recreate` re-reads host env
    # so RELOAD=1 is actually picked up by the container's entrypoint.
    cmd = ["docker", "compose", "-f", str(compose_file),
           "up", "-d", "--force-recreate", "--no-deps", DOCKER_COMPOSE_SERVICE]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"docker compose up --force-recreate failed:\n{result.stderr}")
    print(f"  [reload] container recreated; waiting for Fuseki to finish loading ...",
          file=sys.stderr)

    count_q = "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"

    def _wait_stable(ep: str, min_triples: int = 1000) -> int:
        """Poll ep until COUNT(*) >= min_triples and is stable for two consecutive
        reads 5s apart. The fq: endpoint does a GSP-PUT after Fuseki starts, so
        ASK{} returning 200 is NOT enough — we need actual data to be there."""
        prev = -1
        for attempt in range(180):  # up to 6 min
            try:
                n = _sparql_count(ep, count_q, timeout=10)
                if n >= min_triples and n == prev:
                    print(f"  [reload] {ep} stable at {n:,} triples (attempt {attempt+1})",
                          file=sys.stderr)
                    return n
                if n != prev:
                    print(f"  [reload] {ep}: {n:,} triples (still loading ...)",
                          file=sys.stderr)
                prev = n
            except Exception as exc:
                print(f"  [reload] {ep} not yet reachable: {exc}", file=sys.stderr)
                prev = -1
            time.sleep(5)
        raise RuntimeError(f"Fuseki endpoint {ep} never stabilised after reload")

    # Composition (xloader bulk-load) is fast; fq: GSP-PUT happens after Fuseki
    # is up, so it takes longer — wait for both.
    comp_triples = _wait_stable(baseline_endpoint, min_triples=100_000) if baseline_endpoint else None
    fq_triples   = _wait_stable(endpoint,          min_triples=100_000)
    print(f"  [reload] live triple counts: fq={fq_triples:,}"
          + (f" composition={comp_triples:,}" if comp_triples else ""),
          file=sys.stderr)
    return {"fq_triples": fq_triples, "composition_triples": comp_triples}


def _count_composition_statements() -> dict:
    """Count CompositionStatement individuals via rdflib (no live endpoint needed)."""
    import rdflib
    from rdflib import RDF, Namespace

    futns = Namespace(FUT)
    g = rdflib.Graph()
    for f in sorted(glob.glob(str(COMP_DIR / "*.ttl"))):
        g.parse(f, format="turtle")
    n_stmts = len(set(g.subjects(RDF.type, futns.CompositionStatement)))
    return {"n_statements": n_stmts}


def _count_levels() -> dict:
    """Count per-level subclass classes (Product/Component/Material/Element) via rdflib."""
    import rdflib
    from rdflib import RDFS, Namespace

    futns = Namespace(FUT)
    g = rdflib.Graph()
    for f in sorted(QUERY_TTL.parent.glob("*.ttl")):
        g.parse(str(f), format="turtle")
    out = {}
    for lvl, col in (("Product", "n_products"), ("Component", "n_components"),
                     ("Material", "n_materials"), ("Element", "n_elements")):
        out[col] = len(set(g.subjects(RDFS.subClassOf, futns[lvl])))
    return out


# ---------------------------------------------------------------------------
# Query timing (KPI #1). urllib POST; no SPARQLWrapper, no hard server dependency.
# ---------------------------------------------------------------------------
def _post_sparql(endpoint: str, query: str, timeout: float = 120.0) -> float:
    """POST a SPARQL query, request JSON results, return round-trip seconds.
    Raises on HTTP/network error so the caller can record a clear failure."""
    data = urllib.parse.urlencode({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=data,
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()
    return time.perf_counter() - t0


def _median_iqr_ms(samples) -> tuple:
    """median and IQR (q3-q1) in milliseconds for a list of second-durations."""
    if not samples:
        return (None, None)
    ms = sorted(s * 1000.0 for s in samples)
    median = statistics.median(ms)
    if len(ms) >= 4:
        q = statistics.quantiles(ms, n=4)   # [q1, q2, q3]
        iqr = q[2] - q[0]
    else:
        iqr = ms[-1] - ms[0]
    return (median, iqr)


def _time_query_set(endpoint: str, queries: dict, reps: int) -> tuple:
    """Run each query in `queries` `reps` times against `endpoint`, discarding the
    first (warm-up) of each. Return (median_ms, iqr_ms) pooled across queries, or
    (None, None) on any failure. Logs failures; never raises."""
    all_samples = []
    for name, q in queries.items():
        for i in range(reps):
            try:
                dt = _post_sparql(endpoint, q)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                print(f"  [query] {name} rep {i} failed against {endpoint}: {exc}",
                      file=sys.stderr)
                return (None, None)
            if i == 0:
                continue                    # warm-up discarded
            all_samples.append(dt)
    return _median_iqr_ms(all_samples)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
def _parse_points(points_arg: str) -> list:
    """Parse --points ('all' or a comma list of cumulative sizes) into a sorted,
    deduped list of ints in [1, len(DRIVETRAIN_ORDER)]."""
    n_max = len(DRIVETRAIN_ORDER)
    if points_arg.strip().lower() == "all":
        return list(range(1, n_max + 1))
    out = []
    for tok in points_arg.split(","):
        tok = tok.strip()
        if not tok:
            continue
        k = int(tok)
        if k < 1 or k > n_max:
            print(f"WARNING: skipping out-of-range point {k} "
                  f"(must be 1..{n_max})", file=sys.stderr)
            continue
        out.append(k)
    return sorted(set(out))


def _selection_for(k: int) -> list:
    """The cumulative drivetrain selection for size k (the first k tokens)."""
    return DRIVETRAIN_ORDER[:k]


def _point_id(selection: list) -> str:
    if len(selection) == len(DRIVETRAIN_ORDER):
        return "elv-full"
    return "elv-" + "-".join(s.lower() for s in selection)


# ---------------------------------------------------------------------------
# Environment record (reproducibility — design §3 "honesty guards").
# ---------------------------------------------------------------------------
def _environment() -> dict:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "bench_mem_cap_gb": os.getenv("BENCH_MEM_CAP_GB"),
        "drivetrain_order": DRIVETRAIN_ORDER,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


# ---------------------------------------------------------------------------
# Per-point run
# ---------------------------------------------------------------------------
def _run_point(k: int, years: list, reps: int, endpoint, baseline_endpoint) -> dict:
    selection = _selection_for(k)
    pid = _point_id(selection)
    print(f"\n=== point {pid}: --files {' '.join(selection)} "
          f"--years {' '.join(map(str, years))} ===", file=sys.stderr)

    import build_instances   # triggers install_memory_guard; that's expected/fine

    # 1. derive (DESTRUCTIVE) — wall-clock + peak RSS.
    t0 = time.perf_counter()
    info = build_instances.build_futuram(files=selection, years=list(years))
    derive_seconds = time.perf_counter() - t0
    peak_rss_mb = _peak_rss_mb()
    info = info or {}
    print(f"  derive: {derive_seconds:.1f}s  peak RSS {peak_rss_mb:.0f} MB  "
          f"serve_corpus fq_triples={info.get('fq_triples')}", file=sys.stderr)

    # 2. reload Fuseki and query live triple counts; level counts still via rdflib.
    stmts = _count_composition_statements()
    lvl = _count_levels()
    if endpoint:
        live = _reload_fuseki_and_count(endpoint, baseline_endpoint)
        fq_triples = live["fq_triples"]
        comp_triples = live["composition_triples"]
    else:
        # No Fuseki available — fall back to rdflib counts with a warning.
        print("  [count] WARNING: no --endpoint, falling back to rdflib triple counts",
              file=sys.stderr)
        import rdflib as _rdflib
        cg = _rdflib.Graph()
        for f in sorted(glob.glob(str(COMP_DIR / "*.ttl"))):
            cg.parse(f, format="turtle")
        comp_triples = len(cg)
        fq_triples = info.get("fq_triples") or sum(
            len(_rdflib.Graph().parse(str(f), format="turtle"))
            for f in sorted(QUERY_TTL.parent.glob("*.ttl"))
        )
    ratio = (fq_triples / comp_triples) if comp_triples else None

    # 3. query timing (optional — Fuseki already warm from step 2).
    q_fq_med = q_fq_iqr = q_base_med = q_base_iqr = None
    if endpoint:
        q_fq_med, q_fq_iqr = _time_query_set(endpoint, QUERIES_FQ, reps)
    else:
        print("  query timing: SKIPPED (no --endpoint)", file=sys.stderr)
    if baseline_endpoint:
        q_base_med, q_base_iqr = _time_query_set(
            baseline_endpoint, QUERIES_BASELINE, reps)

    return {
        "point_id": pid,
        "drivetrains": "|".join(selection),
        "n_products": lvl["n_products"],
        "n_components": lvl["n_components"],
        "n_materials": lvl["n_materials"],
        "n_elements": lvl["n_elements"],
        "n_statements": stmts["n_statements"],
        "composition_triples": comp_triples,
        "fq_triples": fq_triples,
        "fq_comp_ratio": round(ratio, 4) if ratio is not None else None,
        "derive_seconds": round(derive_seconds, 3),
        "peak_rss_mb": round(peak_rss_mb, 1),
        "q_fq_median_ms": round(q_fq_med, 3) if q_fq_med is not None else None,
        "q_fq_iqr_ms": round(q_fq_iqr, 3) if q_fq_iqr is not None else None,
        "q_baseline_median_ms": round(q_base_med, 3) if q_base_med is not None else None,
        "q_baseline_iqr_ms": round(q_base_iqr, 3) if q_base_iqr is not None else None,
    }


def _write_csv(out_path: pathlib.Path, rows: list):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        fh.write("# FutuRaM scaling benchmark — drivetrain axis (cumulative).\n")
        fh.write("# derive_seconds is wall-clock and is EXPECTED SUPER-LINEAR in N\n")
        fh.write("#   (pooled re-aggregation re-aggregates the whole instance pool).\n")
        fh.write("# peak_rss_mb is the build-phase high-water (the RAM that scales),\n")
        fh.write("#   not serving RAM (TDB2 mmaps the store ~flat).\n")
        fh.write("# q_* are null unless --endpoint / --baseline-endpoint were given.\n")
        fh.write(f"# environment sidecar: {ENV_SIDECAR.name}\n")
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nwrote {len(rows)} row(s) -> {out_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--points", default="1,2,3,6",
        help="cumulative drivetrain counts (comma list) or 'all'. "
             f"Default '1,2,3,6'. Order: {','.join(DRIVETRAIN_ORDER)}.")
    ap.add_argument(
        "--years", type=int, nargs="+", default=DEFAULT_YEARS,
        help=f"fixed production-year slices (default {DEFAULT_YEARS}); "
             "pinned so points differ only by the drivetrain axis.")
    ap.add_argument(
        "--reps", type=int, default=6,
        help="query repetitions per query (first is discarded as warm-up). "
             "Default 6. Only used when --endpoint is given.")
    ap.add_argument(
        "--endpoint", default=None,
        help="SPARQL endpoint URL for the SERVED fq: graph. If omitted, query "
             "timing is SKIPPED and q_fq_* columns are null.")
    ap.add_argument(
        "--baseline-endpoint", default=None,
        help="SPARQL endpoint URL for the RAW composition graph (the contrast). "
             "If omitted, q_baseline_* columns are null.")
    ap.add_argument(
        "--out", default=str(DEFAULT_OUT),
        help=f"output CSV path (default {DEFAULT_OUT}).")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="print the plan (points + selections) and exit WITHOUT building.")
    ap.add_argument(
        "--yes", action="store_true",
        help="confirm the DESTRUCTIVE builds (rewrites the committed dataset). "
             "Required to actually run; --dry-run does not need it.")
    args = ap.parse_args(argv)

    points = _parse_points(args.points)
    if not points:
        ap.error("no valid points selected (see --points).")

    # --- the plan (always printed) ---
    print("FutuRaM scaling benchmark — plan", file=sys.stderr)
    print(f"  drivetrain order : {', '.join(DRIVETRAIN_ORDER)}", file=sys.stderr)
    print(f"  years (fixed)    : {args.years}", file=sys.stderr)
    print(f"  reps             : {args.reps}", file=sys.stderr)
    print(f"  endpoint         : {args.endpoint or '(none — query timing SKIPPED)'}",
          file=sys.stderr)
    print(f"  baseline-endpoint: {args.baseline_endpoint or '(none — baseline SKIPPED)'}",
          file=sys.stderr)
    print(f"  out              : {args.out}", file=sys.stderr)
    print(f"  points           : {len(points)}", file=sys.stderr)
    for k in points:
        sel = _selection_for(k)
        print(f"    - {_point_id(sel):16s}  k={k}  --files {' '.join(sel)}",
              file=sys.stderr)

    if args.dry_run:
        print("\n--dry-run: nothing built.", file=sys.stderr)
        return 0

    if not args.yes:
        ap.error(
            "REFUSING to run: the derive is DESTRUCTIVE — it rewrites the "
            "git-committed fuseki/futuram/data/composition/*.ttl AND "
            "fuseki/futuram/data/query/futuram.ttl. Re-run with --yes to "
            "proceed, or --dry-run to just see the plan.")

    print("\n*** WARNING: building will OVERWRITE the committed dataset under\n"
          f"    {COMP_DIR}\n    {QUERY_TTL}\n"
          "    (regenerate/restore from git afterwards if you need the original). ***\n",
          file=sys.stderr)

    # environment sidecar (reproducibility)
    env = _environment()
    ENV_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    ENV_SIDECAR.write_text(json.dumps(env, indent=2))
    print(f"wrote environment -> {ENV_SIDECAR}", file=sys.stderr)

    rows = []
    for k in points:
        try:
            rows.append(_run_point(k, args.years, args.reps,
                                    args.endpoint, args.baseline_endpoint))
        except Exception as exc:            # don't lose earlier points on a late failure
            print(f"  POINT k={k} FAILED: {exc!r} — logged, continuing.",
                  file=sys.stderr)
        # write incrementally so a long run isn't all-or-nothing
        if rows:
            _write_csv(pathlib.Path(args.out), rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())

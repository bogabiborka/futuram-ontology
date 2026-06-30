# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl", "openpyxl"]
# ///
"""build_instances — regenerate the served `futuram` datasource's committed TTL.

DEFAULT (no args) = bench year slices 2010/2020/2025/2030/2050; full 1980-2050
needs --full-span + env BUILD_FULL_SPAN=i-really-mean-it (can OOM). MC is OFF.
"""
import argparse
import os
import pathlib
import shutil
import sys

from etl import buckets
from etl import csv_to_rdf as X
from etl import serve_corpus as G
try:
    from common.memcap import install_memory_guard
except Exception:                       # common not importable -> no cap (correct)
    def install_memory_guard(*a, **k):  # noqa: D401
        return

from etl import elv_csvs
ROOT = pathlib.Path(__file__).resolve().parent.parent
FUSEKI = ROOT / "fuseki"
ELV_CSVS = elv_csvs()

# The bench data span: the discrete production-year slices the benchmark loads and
# every test/verification expects. This is the DEFAULT build — `build_instances
# futuram` with no year args builds exactly these.
BENCH_YEARS = [2010, 2020, 2025, 2030, 2050]

# Opt-in gate for the heavy full 1980-2050 bucketed span. Both must be present:
# the --full-span flag AND this env value. A bare invocation can never trigger it.
FULL_SPAN_ENV = "BUILD_FULL_SPAN"
FULL_SPAN_TOKEN = "i-really-mean-it"


def build_futuram(width=20, year_min=None, year_max=None, years=None,
                  files=None):
    comp = FUSEKI / "futuram" / "data" / "composition"
    query = FUSEKI / "futuram" / "data" / "query"
    # Remove only the ABox (elv-*.ttl) files — TBox/bridge/metalwheel/ChEBI files
    # are constant across drivetrain subsets and must stay for Fuseki to serve
    # the full graph including TBox + inferred triples.
    if comp.exists():
        for f in comp.glob("elv-*.ttl"):
            f.unlink()
    comp.mkdir(parents=True, exist_ok=True)
    query.mkdir(parents=True, exist_ok=True)

    # Optional file filter (e.g. BEV Petrol) — restrict to those input CSVs only.
    # Matched case-insensitively against each CSV's file token (ELV_..._<TOKEN>).
    csvs = ELV_CSVS
    if files:
        want = {f.lower() for f in files}
        csvs = [c for c in ELV_CSVS if c.stem.split("_")[-1].lower() in want]
        missing = want - {c.stem.split("_")[-1].lower() for c in csvs}
        if missing:
            print(f"WARNING: no ELV CSV for file(s): {sorted(missing)}")
    if not csvs:
        print("no ELV CSVs to build (none under data/, or none matched the "
              "--files filter)")
        return

    if years:
        # DISCRETE production years — each becomes its own single-year fq: slice
        # (elv<DT>_Y<year>), filtered at the CSV-read stage so only these years are
        # materialised. One flat composition .ttl per drivetrain.
        yrs = sorted(set(years))
        for csv in csvs:
            dt = csv.stem.split("_")[-1].lower()    # BEV -> bev
            X.to_graph(csv, sid=f"elv-{dt}", years=set(yrs),
                       canonicalize=True).serialize(
                destination=str(comp / f"elv-{dt}.ttl"), format="turtle")
            print(f"  {dt}: years {yrs}")
    else:
        # HARD-clamp to the fleet window (1980-2050): the CSVs span 1980-2050 but
        # only this window is ever served. A caller may narrow within it, never
        # widen past. Contiguous `width`-year buckets per drivetrain.
        year_min, year_max = buckets._clamp_window(year_min, year_max)
        for csv in csvs:
            dt = csv.stem.split("_")[-1].lower()    # BEV -> bev
            cat = buckets.export_buckets(csv, comp / dt, width=width,
                                         year_min=year_min, year_max=year_max)
            print(f"  {dt}: {len(cat['buckets'])} buckets "
                  f"({year_min}-{year_max})")

    # ONE-SHOT global fq: build over ALL drivetrains (the FAST from-scratch path):
    # serve_corpus aggregates ONCE, pooling cross-drivetrain shared components via
    # DrivetrainMeanStrategy. add_source exists for ADDITIVITY, not speed.
    info = G.serve_corpus(comp, query / "futuram.ttl")
    print(f"futuram global fq: {info['instances']} instances, "
          f"{info['classes']} classes, {info['fq_triples']} triples -> {info['out']}")


def main(argv=None):
    # Hard memory cap (watchdog): the all-drivetrain fq derive can run away and
    # OOM the machine (observed 130 GB). Abort cleanly past BENCH_MEM_CAP_GB
    # (default 36 GB; 0 disables) instead of swapping the box to death.
    install_memory_guard(label="build_instances")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("instance", choices=["futuram"])
    # The heavy full-span bucketing (--year-min/--year-max/--width) is ONLY reachable
    # behind --full-span (+ the env token); it can only narrow within 1980-2050.
    ap.add_argument("--year-min", type=int, default=None)
    ap.add_argument("--year-max", type=int, default=None)
    ap.add_argument("--width", type=int, default=20)
    ap.add_argument("--years", type=int, nargs="+", default=None,
                    help="DISCRETE production years; each becomes its own single-year "
                         "fq: slice. Defaults to the bench years "
                         f"{BENCH_YEARS} when omitted.")
    ap.add_argument("--full-span", action="store_true",
                    help="Build the EXPENSIVE full 1980-2050 bucketed span instead of "
                         "the bench years. Requires env "
                         f"{FULL_SPAN_ENV}={FULL_SPAN_TOKEN} as a second confirmation.")
    ap.add_argument("--files", nargs="+", default=None,
                    help="build only these input CSV files, by their trailing "
                         "token (e.g. --files BEV Petrol). Default: all ELV CSVs.")
    args = ap.parse_args(argv)
    if args.instance != "futuram":
        return

    if args.full_span:
        # Two-key launch: the flag alone is not enough — the env token must also be
        # set, so the heavy span can never start by accident or from a stale script.
        if os.environ.get(FULL_SPAN_ENV) != FULL_SPAN_TOKEN:
            ap.error(
                "--full-span builds the heavy 1980-2050 span (millions of triples, "
                "can OOM). It is intentionally hard to start: re-run with the env "
                f"confirmation set, e.g.\n    {FULL_SPAN_ENV}={FULL_SPAN_TOKEN} "
                "uv run build_instances.py futuram --full-span")
        if args.years:
            ap.error("--years and --full-span are mutually exclusive: --full-span "
                     "builds the bucketed span, --years builds discrete slices.")
        print(f"FULL-SPAN build (1980-2050, width-{args.width} buckets) — heavy.",
              file=sys.stderr)
        build_futuram(width=args.width, year_min=args.year_min,
                      year_max=args.year_max, years=None, files=args.files)
        return

    # DEFAULT (and the only non-gated) path: discrete year slices. No years given
    # -> the bench set. --year-min/--year-max/--width are full-span-only knobs and
    # are ignored here (they have no meaning for discrete slices).
    if args.year_min is not None or args.year_max is not None:
        ap.error("--year-min/--year-max only apply to --full-span; for the default "
                 "discrete build, pass the years directly with --years.")
    years = args.years or BENCH_YEARS
    build_futuram(years=years, files=args.files)


if __name__ == "__main__":
    main()

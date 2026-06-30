# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "openpyxl"]
# ///
"""buckets — chunk a dataset's composition RDF into N-year buckets + catalog, and
route queries to the right file(s). The YEAR-INVARIANT material->element chemistry
is factored OUT into ONE shared sibling (EM_SHARED_NAME), merged back on load.
"""
import json
import pathlib
import sys


from rdflib import RDF

from etl import csv_to_rdf as X
from etl.composition_rdf import composition_rdf
from common import pipeline

BUCKET_YEARS = 20
CATALOG_NAME = "catalog.json"

# HARD year window for the ELV fleet (1980-2050): the full span is served so the
# historical composition trend stays discriminating. A CLAMP, not a default —
# every build is intersected with it; a caller may narrow within it, never widen.
FLEET_YEAR_MIN = 1980
FLEET_YEAR_MAX = 2050


def _clamp_window(year_min, year_max):
    """Intersect a requested [year_min, year_max] (either may be None) with the
    hard fleet window [FLEET_YEAR_MIN, FLEET_YEAR_MAX]. The result never widens
    past the fleet window; None means 'use the fleet bound'."""
    lo = FLEET_YEAR_MIN if year_min is None else max(year_min, FLEET_YEAR_MIN)
    hi = FLEET_YEAR_MAX if year_max is None else min(year_max, FLEET_YEAR_MAX)
    return lo, hi

def bucket_of(year, width=BUCKET_YEARS):
    """The (lo, hi) inclusive year window a year falls in (lo aligned to width)."""
    lo = (year // width) * width
    return (lo, lo + width - 1)


def bucket_label(lo, hi):
    return f"{lo}-{hi}"


def _years_in(dataset_path):
    """All production years present in the dataset (one pass)."""
    rows = X.read_rows(pathlib.Path(dataset_path))
    years = set()
    for r in rows:
        y = X._row_year(r)
        if y:
            years.add(y)
    return sorted(years)


def _doc_classes(doc):
    """The whole/part CLASSES a doc covers (for catalog class-routing): every
    node's class plus the subclass-edge subs/sups, so both a time slice
    (V0301030105_Y2026) and its base (V0301030105) route to the bucket."""
    classes = {spec["class"] for spec in doc["nodes"].values()}
    for sub_c, sups in (doc.get("subclass_of") or {}).items():
        classes.add(sub_c)
        classes.update([sups] if isinstance(sups, str) else sups)
    return sorted(classes)


def export_buckets(dataset_path, outdir, width=BUCKET_YEARS, canonicalize=True,
                   full_metadata=True, year_min=None, year_max=None):
    """Write one composition-RDF .ttl per `width`-year bucket + catalog.json, plus
    ONE shared material-element.ttl (year-invariant chemistry, over the full range).
    year_min/year_max hard-clamped to the fleet range (1980-2050). Returns catalog."""
    dataset_path = pathlib.Path(dataset_path)
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    year_min, year_max = _clamp_window(year_min, year_max)
    years = [y for y in _years_in(dataset_path)
             if year_min <= y <= year_max]
    if not years:
        raise ValueError(
            f"{dataset_path}: no production years in clamped window "
            f"{year_min}-{year_max}")

    # group years into buckets
    windows = sorted({bucket_of(y, width) for y in years})
    catalog = {"dataset": dataset_path.name, "width": width,
               "shared": [pipeline.EM_SHARED_NAME], "buckets": []}

    # the year-invariant m->e layer, ONCE from the full selected range. The ETL
    # doc path (transform -> composition_rdf, NO oracle): split the full-range doc
    # and emit ONLY its m->e sub-doc.
    full_doc = X.transform_doc(dataset_path, sid=f"{dataset_path.stem}_em",
                               years=set(years), canonicalize=canonicalize)
    _structural, em_doc = X.split_em_doc(full_doc)
    em_g = composition_rdf(em_doc, full_metadata=full_metadata)
    em_g.serialize(destination=str(outdir / pipeline.EM_SHARED_NAME),
                   format="turtle")
    catalog["shared_stats"] = {"n_triples": len(em_g)}

    for lo, hi in windows:
        win_years = {y for y in years if lo <= y <= hi}
        doc = X.transform_doc(dataset_path, sid=f"elv_{lo}_{hi}",
                              years=win_years, canonicalize=canonicalize)
        if not doc["statements"]:
            continue
        # m->e statements live in the shared file; this window keeps only the
        # year-dependent structural layers (+ ALL nodes, for routing). Its
        # axis_values marker lets the builder slice the drivetrain axis from RDF.
        structural, em = X.split_em_doc(doc)
        g = composition_rdf(structural, full_metadata=full_metadata,
                            axis_values=structural.get("axis_values"))
        fname = f"{bucket_label(lo, hi)}.ttl"
        g.serialize(destination=str(outdir / fname), format="turtle")

        catalog["buckets"].append({
            "file": fname,
            "year_lo": lo, "year_hi": hi,
            "years": sorted(win_years),
            "classes": _doc_classes(structural),
            "n_nodes": len(structural["nodes"]),
            "n_stmts": len(structural["statements"]),
            "n_em_shared": len(em["statements"]),
            "n_triples": len(g),
        })

    (outdir / CATALOG_NAME).write_text(json.dumps(catalog, indent=2))
    return catalog


class BucketRouter:
    """Given a catalog, return the bucket files relevant to a query's years
    and/or classes — the "know which RDF file to load" pipeline."""

    def __init__(self, catalog_path):
        self.dir = pathlib.Path(catalog_path).parent
        self.catalog = json.loads(pathlib.Path(catalog_path).read_text())
        self.buckets = self.catalog["buckets"]
        # the year-invariant shared layer(s) every routed bucket needs (older
        # catalogs predate the m->e split and have none)
        self.shared = [self.dir / n for n in self.catalog.get("shared", [])]

    def shared_files(self):
        """The shared (year-invariant) sibling files, e.g. material-element.ttl;
        every routed bucket must be loaded together with these."""
        return list(self.shared)

    def files_for(self, years=None, classes=None):
        """Bucket FILE PATHS whose year window intersects `years` (a set/range or
        single int) AND that cover at least one of `classes` (if given). With no
        constraints, returns every bucket."""
        if isinstance(years, int):
            years = {years}
        want_classes = set(classes) if classes else None
        out = []
        for b in self.buckets:
            if years is not None:
                window = set(range(b["year_lo"], b["year_hi"] + 1))
                if not (set(years) & window):
                    continue
            if want_classes is not None and not (want_classes & set(b["classes"])):
                continue
            out.append(self.dir / b["file"])
        return out

    def all_files(self):
        return [self.dir / b["file"] for b in self.buckets]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Export N-year composition-RDF buckets")
    ap.add_argument("input", type=pathlib.Path, help="Excel/CSV dataset")
    ap.add_argument("-o", "--outdir", type=pathlib.Path, required=True)
    ap.add_argument("--width", type=int, default=BUCKET_YEARS,
                    help=f"years per bucket (default {BUCKET_YEARS})")
    ap.add_argument("--no-canonicalize", action="store_true")
    args = ap.parse_args(argv)
    cat = export_buckets(args.input, args.outdir, width=args.width,
                         canonicalize=not args.no_canonicalize)
    print(f"wrote {len(cat['buckets'])} buckets to {args.outdir} "
          f"(catalog: {args.outdir / CATALOG_NAME})")
    for b in cat["buckets"]:
        print(f"  {b['file']}: {b['n_stmts']} stmts, {b['n_triples']} triples, "
              f"{len(b['classes'])} classes")


if __name__ == "__main__":
    main()

#!/usr/bin/env -S uv run --quiet --with rdflib --with pyshacl --with owlrl python
# /// script
# requires-python = ">=3.11"
# dependencies = ["rdflib", "pyshacl", "owlrl"]
# ///
"""Assemble + validate the data-CI release assets (the GitHub Release is the
product). The two artifacts ARE the two served Fuseki datasets, each merged into
one Turtle file:

    futuram-baseline-<version>.ttl          the composition dataset (fuseki .../composition)
    futuram-query-optimized-<version>.ttl   the query dataset       (fuseki .../query)

The <version> is the release tag (e.g. v0.2.0), passed via --version (the
workflow derives it from the git ref); it falls back to "dev" when unset.

Both are gated before they publish: the SHACL served-graph gate and the
served↔composition pair-binding check must pass.

Usage:
    uv run scripts/ci_release_assets.py --out dist/ --version v0.2.0   # assemble + validate
    uv run scripts/ci_release_assets.py --validate-only                # gate, no file output

Exit: 0 = written and conformant; 1 = validation failure / missing input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from rdflib import Graph, URIRef

PROV = "http://www.w3.org/ns/prov#"
FQ = "https://www.purl.org/futuram/query#"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

CONFIG = ROOT / "config.json"

TBOX_DIR = ROOT / "ontology" / "tbox"
SHAPES_DIR = ROOT / "shapes"

# The two served Fuseki datasets — each a directory of .ttl that Fuseki loads.
# They ARE the release artifacts (merged to one Turtle file each on publish).
BASELINE_DIR = ROOT / "fuseki" / "futuram" / "data" / "composition"
QUERY_DIR = ROOT / "fuseki" / "futuram" / "data" / "query"

RAW_ABOX_DIR = BASELINE_DIR
SERVED_ABOX = QUERY_DIR / "futuram.ttl"


def _merge(paths) -> Graph:
    g = Graph()
    for p in paths:
        g.parse(ROOT / p if not Path(p).is_absolute() else p, format="turtle")
    return g


def load_composition_tbox(cfg) -> Graph:
    """Composition-statement schema bundle: the ground vocabulary + its shapes.
    Imports are resolved by co-loading the modules."""
    cs = cfg["compositionSchema"]
    return _merge(cs["modules"] + cs["shapes"])


def load_query_tbox(cfg) -> Graph:
    """futuram-query (fq:) schema bundle: the query TBox + its shapes. The
    resolver/plugin deriver is code, versioned by querySchema.version."""
    qs = cfg["querySchema"]
    return _merge(qs["modules"] + qs["shapes"])


def load_raw_abox() -> Graph:
    return _merge(sorted(RAW_ABOX_DIR.glob("*.ttl")))


def load_served_abox() -> Graph:
    if not SERVED_ABOX.exists():
        sys.exit(f"error: served ABox missing: {SERVED_ABOX}\n"
                 "Run the derive step (etl.serve_corpus) before release.")
    return _merge([SERVED_ABOX])


def verify_pair(served: Graph, raw: Graph) -> bool:
    """Check the served/raw ABoxes are a bound pair: every
    fq:derivedFromStatement pointer in the served graph must resolve to a
    statement in the raw set. A dangling pointer means they are out of sync."""
    pred = URIRef(FQ + "derivedFromStatement")
    targets = {o for _, _, o in served.triples((None, pred, None))
               if isinstance(o, URIRef)}
    if not targets:
        print("verify pair: served graph has no fq:derivedFromStatement pointers "
              "(nothing to bind) — skipping.")
        return True
    raw_subjects = set(raw.subjects())
    dangling = sorted(str(t) for t in targets if t not in raw_subjects)
    ok = not dangling
    print(f"verify pair: {len(targets)} derivedFromStatement pointers, "
          f"{len(dangling)} dangling ->", "PASS" if ok else "FAIL")
    for d in dangling[:10]:
        print("   dangling:", d)
    return ok


def gate(served: Graph, comp_tbox: Graph, query_tbox: Graph) -> bool:
    """SHACL gate over the derived graph against BOTH schema bundles."""
    from common.pipeline import validate_served
    data = served + comp_tbox + query_tbox
    report = validate_served(data)
    ok = bool(getattr(report, "conforms", report))
    print("SHACL served-graph gate:", "PASS" if ok else "FAIL")
    if not ok:
        print(getattr(report, "text", report))
    return ok


def _sha256(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, help="directory to write release assets")
    ap.add_argument("--version", default="dev",
                    help="release version for the asset names (the git tag, e.g. v0.2.0)")
    ap.add_argument("--validate-only", action="store_true",
                    help="run the gate only, write nothing")
    args = ap.parse_args(argv)

    cfg = json.loads(CONFIG.read_text())
    comp_ver = cfg["compositionSchema"]["version"]
    query_ver = cfg["querySchema"]["version"]
    ds = cfg["dataset"]
    dataset_id = ds["id"]
    pinned = ds.get("pinnedCompositionSchema", comp_ver)
    if pinned != comp_ver:
        print(f"WARNING: dataset pins composition schema {pinned} but config is "
              f"{comp_ver}; rebuild the dataset against the current schema.")

    comp_tbox = load_composition_tbox(cfg)
    query_tbox = load_query_tbox(cfg)
    served = load_served_abox()
    raw = load_raw_abox()

    if not gate(served, comp_tbox, query_tbox):
        return 1
    if not verify_pair(served, raw):
        return 1

    if args.validate_only:
        print("validate-only: conformant + pair bound, no assets written.")
        return 0

    # The two artifacts ARE the two served Fuseki datasets, each merged to one
    # Turtle file and named by the release version (the git tag).
    ver = args.version
    out = args.out or (ROOT / "dist")
    out.mkdir(parents=True, exist_ok=True)

    baseline = _merge(sorted(BASELINE_DIR.glob("*.ttl")))
    query = _merge(sorted(QUERY_DIR.glob("*.ttl")))

    baseline_path = out / f"futuram-baseline-{ver}.ttl"
    query_path = out / f"futuram-query-optimized-{ver}.ttl"
    baseline.serialize(baseline_path, format="turtle")
    query.serialize(query_path, format="turtle")

    manifest = {
        "version": ver,
        "compositionSchemaVersion": comp_ver,
        "querySchemaVersion": query_ver,
        "datasetId": dataset_id,
        "assets": {
            "baseline": {
                "file": baseline_path.name,
                "sha256": _sha256(baseline_path),
                "triples": len(baseline),
            },
            "queryOptimized": {
                "file": query_path.name,
                "sha256": _sha256(query_path),
                "triples": len(query),
            },
        },
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"wrote {baseline_path.name}  ({len(baseline)} triples)")
    print(f"wrote {query_path.name}  ({len(query)} triples)")
    print(f"wrote {manifest_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

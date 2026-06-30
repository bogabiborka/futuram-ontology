#!/usr/bin/env python3
"""make_error_yaml.py <run_dir> [--source domain.yaml] [--out error-domain.yaml]

Reads a bench run directory, finds all cases that scored incorrect (correct=False),
derives the base case ID (strips __valunc suffix), then extracts those cases from
the source testcases YAML and writes them to --out.

Cases with correct=None (still running / incomplete) are skipped.
Deduplicates by base ID so a __valunc and its plain twin both failing only appear once.
"""
import argparse, glob, json, pathlib, sys
import yaml  # uv run --with pyyaml

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", help="Path to a bench run directory (contains *.json case files)")
    ap.add_argument("--source", default="bench/testcases/domain.yaml",
                    help="Source testcases YAML (default: bench/testcases/domain.yaml)")
    ap.add_argument("--out", default="bench/testcases/error-domain.yaml",
                    help="Output YAML path (default: bench/testcases/error-domain.yaml)")
    args = ap.parse_args()

    run_dir = pathlib.Path(args.run_dir)
    source  = pathlib.Path(args.source)
    out     = pathlib.Path(args.out)

    # Collect failed base IDs from the run
    failed_base_ids: set[str] = set()
    skipped_incomplete = 0
    for f in sorted(glob.glob(str(run_dir / "*.json"))):
        if pathlib.Path(f).name in ("results.json", "run.log"):
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        # progress-only files (still running) have no "correct" but have "case_id"
        correct = d.get("correct", None if "case_id" in d else "skip")
        if correct is None:
            skipped_incomplete += 1
            case_id = d.get("case_id", "")
            base_id = case_id.replace("__valunc", "")
            failed_base_ids.add(base_id)
            continue
        if correct is False:
            case_id = d.get("case_id", "")
            # strip __valunc suffix to get the base ID
            base_id = case_id.replace("__valunc", "")
            failed_base_ids.add(base_id)

    if skipped_incomplete:
        print(f"Note: {skipped_incomplete} case(s) with correct=None (incomplete) included as failed",
              file=sys.stderr)

    if not failed_base_ids:
        print("No failed cases found — no output written.")
        return

    print(f"Failed base IDs ({len(failed_base_ids)}): {sorted(failed_base_ids)}")

    # Load source YAML
    text = source.read_text()
    header = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#"))
    doc = yaml.safe_load(text)
    cases = doc if isinstance(doc, list) else doc.get("cases", doc)

    # Extract matching cases (both plain and __valunc variants)
    kept = [c for c in cases if
            c.get("id") in failed_base_ids or
            str(c.get("id","")).replace("__valunc","") in failed_base_ids]

    if not kept:
        print(f"None of the failed IDs found in {source} — check --source path.", file=sys.stderr)
        sys.exit(1)

    print(f"Extracted {len(kept)} case(s) from {source} → {out}")

    if isinstance(doc, list):
        out_doc = kept
    else:
        out_doc = dict(doc)
        out_doc["cases"] = kept

    out.write_text(header + yaml.safe_dump(out_doc, sort_keys=False, allow_unicode=True,
                                           default_flow_style=False))

if __name__ == "__main__":
    main()

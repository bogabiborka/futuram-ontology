# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml"]
# ///
"""Freeze the oracle surface into golden fixtures (tests/expected/<version>/).

Every scenario YAML, the oneCar RDF fixture and a multi-year CSV slice are run
through the LIVE oracle and their full ground-truth surface (aggregate, element_in_whole,
conservation, coarse_fine) written to reviewed JSON; test_golden_oracle.py checks it (1e-9).

Two modes (--version selects tests/expected/<version>/, default ACTIVE_VERSION):
    uv run scripts/freeze_oracle.py                 # VERIFY: exit 1 on drift
    uv run scripts/freeze_oracle.py --regenerate    # rewrite fixtures (review the git diff!)
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))     # oracle/etl/builder/common packages
sys.path.insert(0, str(ROOT / "tests"))   # tests-local golden reference

import golden # noqa: E402


def _close(a, b):
    return math.isclose(a, b, rel_tol=golden.REL_TOL, abs_tol=golden.ABS_TOL)


def diff(expected, got, where=""):
    """Yield human-readable drift lines between two surface trees."""
    if isinstance(expected, dict) and isinstance(got, dict):
        for k in sorted(set(expected) | set(got)):
            w = f"{where}.{k}" if where else str(k)
            if k not in expected:
                yield f"UNEXPECTED {w} = {got[k]!r}"
            elif k not in got:
                yield f"MISSING    {w} (expected {expected[k]!r})"
            else:
                yield from diff(expected[k], got[k], w)
    elif isinstance(expected, float) and isinstance(got, (int, float)):
        if not _close(expected, float(got)):
            yield f"DRIFT      {where}: expected {expected!r}, got {got!r}"
    elif expected != got:
        yield f"DRIFT      {where}: expected {expected!r}, got {got!r}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--regenerate", action="store_true",
                    help="(re)write the fixtures instead of verifying")
    ap.add_argument("--version", default=None,
                    help=f"fixture set under tests/expected/ "
                         f"(default {golden.ACTIVE_VERSION})")
    ap.add_argument("--only", nargs="*",
                    help="restrict to these source ids")
    args = ap.parse_args()

    out_dir = golden.expected_dir(args.version)
    drifted = []
    for sid, load in golden.iter_sources():
        if args.only and sid not in args.only:
            continue
        path = out_dir / f"{sid}.json"
        got = golden.surface(load())
        if args.regenerate:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
            print(f"wrote {path.relative_to(ROOT)}")
            continue
        if not path.exists():
            drifted.append(sid)
            print(f"[{sid}] no fixture at {path.relative_to(ROOT)} "
                  f"(run with --regenerate)")
            continue
        lines = list(diff(json.loads(path.read_text()), got))
        if lines:
            drifted.append(sid)
            print(f"[{sid}] {len(lines)} difference(s):")
            for ln in lines[:20]:
                print(f"  {ln}")
            if len(lines) > 20:
                print(f"  ... and {len(lines) - 20} more")
        else:
            print(f"[{sid}] ok")
    if drifted:
        print(f"\nDRIFT in {len(drifted)} source(s): {', '.join(drifted)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

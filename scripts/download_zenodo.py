# /// script
# requires-python = ">=3.9"
# ///
"""download_zenodo — fetch the six FutuRaM ELV vehicle-level drivetrain CSVs
(~1.1 GB, uncommitted) from Zenodo record 19493413 into data/ (where
tests/build_instances.py globs them); skips ones already present.

Usage:
    uv run scripts/download_zenodo.py            # into <repo>/data
    uv run scripts/download_zenodo.py -o /tmp    # elsewhere
    uv run scripts/download_zenodo.py --force    # re-download even if present
"""
import argparse
import pathlib
import sys
import urllib.request

RECORD = "19493413"
BASE = f"https://zenodo.org/api/records/{RECORD}/files"
DRIVETRAINS = ["BEV", "Diesel", "HEV", "Others", "Petrol", "PHEV"]
FILES = [f"ELV_1980_2050_{d}.csv" for d in DRIVETRAINS]

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _report(name):
    """A urlretrieve progress hook printing MB downloaded on one line."""
    def hook(block, block_size, total):
        done = block * block_size
        if total > 0:
            pct = min(100, 100 * done / total)
            sys.stdout.write(f"\r  {name}: {done/1e6:7.1f} / {total/1e6:.1f} MB ({pct:4.1f}%)")
        else:
            sys.stdout.write(f"\r  {name}: {done/1e6:7.1f} MB")
        sys.stdout.flush()
    return hook


def download(outdir, force=False):
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    got = 0
    for name in FILES:
        dest = outdir / name
        if dest.exists() and not force:
            print(f"  {name}: present ({dest.stat().st_size/1e6:.1f} MB), skipping")
            continue
        url = f"{BASE}/{name}/content"
        urllib.request.urlretrieve(url, dest, _report(name))
        sys.stdout.write("\n")
        got += 1
    return got


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--outdir", type=pathlib.Path, default=REPO_ROOT / "data",
                    help="where to write the CSVs (default: <repo>/data)")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    args = ap.parse_args(argv)
    n = download(args.outdir, force=args.force)
    print(f"\ndone: {n} file(s) downloaded into {args.outdir}")


if __name__ == "__main__":
    main()

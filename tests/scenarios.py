# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml"]
# ///
"""Load the YAML scenarios and (re)generate their Turtle fixtures.

YAML -> SupplyChain (oracle ground truth) -> fixtures/*.ttl. `ALL` maps scenario
id -> SupplyChain; `uv run tests/scenarios.py` regenerates every fixture.
"""
import pathlib
from oracle.supplychain import SupplyChain
from etl import TEST_INPUT

HERE = pathlib.Path(__file__).resolve().parent
YAML_DIR = TEST_INPUT                     # synthetic scenario YAMLs (etl/input/test)
FIXTURES = HERE / "fixtures"


def load_all():
    """id -> SupplyChain, for every YAML scenario (sorted by filename)."""
    out = {}
    for y in sorted(YAML_DIR.glob("*.yaml")):
        sc = SupplyChain.from_yaml(y)
        out[sc.id] = sc
    return out


ALL = load_all()


def regenerate():
    FIXTURES.mkdir(exist_ok=True)
    for sid, sc in ALL.items():
        cons = sc.conservation()
        cf = sc.coarse_fine()
        header = (f"# GENERATED from scenarios_yaml/{sid}.yaml — DO NOT EDIT.\n"
                  f"# {sc.label}: {getattr(sc, '_note', '')}\n"
                  f"# conservation: " +
                  "; ".join(f"{w}:[{d['min']:.4f}..{d['max']:.4f}"
                            f"{' OVERSHOOT' if d['overshoot'] else ''}]"
                            for w, d in cons.items()))
        if cf:
            header += "\n# coarse/fine: " + "; ".join(
                f"{w}->{p}: coarse[{d['coarse_min']:.3f}] granular[{d['granular_min']:.3f}]"
                f" unknown[{d['unknown_min']:.3f}]"
                f"{' OVERSHOOT' if d['overshoot'] else ''}"
                for (w, p), d in cf.items())
        sc.write(FIXTURES / f"{sid}.ttl", header=header)
        print(f"{sid:24s} {getattr(sc, '_note', '')}")
        print(f"    conservation: { {w: (round(d['min'],4), d['overshoot']) for w,d in cons.items()} }")
        if cf:
            print(f"    coarse/fine : { {f'{w}->{p}': dict(granular=round(d['granular_min'],3), unknown=round(d['unknown_min'],3), overshoot=d['overshoot']) for (w,p),d in cf.items()} }")


if __name__ == "__main__":
    regenerate()

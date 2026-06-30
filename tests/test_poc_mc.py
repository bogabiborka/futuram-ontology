# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "pytest"]
# ///
"""poc Monte-Carlo: poc EXTENDS the builder (poc -> builder) and adds MC. Two
contracts: (1) parity — poc.aggregate_mc reproduces the oracle's aggregate_mc per
(class, element); (2) layering — the core builder does NO MC and never imports poc."""
import pathlib

import pytest
from rdflib import Namespace

import scenarios
from oracle.supplychain import SupplyChain
from etl import TEST_INPUT
from builder.index import build_index
import builder.resolver as resolver
import poc

FQ = Namespace("https://www.purl.org/futuram/query#")
YAMLS = sorted(pathlib.Path(TEST_INPUT).glob("*.yaml"))


# poc MC is an INDEPENDENT reimplementation, not bit-identical to the oracle: the
# oracle samples in the authored unit then scales, poc samples in kg/kg directly, and
# scaling does not commute with a non-linear draw — so best/lo/hi can drift a few %.
MC_TOL = dict(rel=0.05, abs=0.01)          # sampled percentiles: MC + scaling drift


@pytest.mark.parametrize("yaml_path", YAMLS, ids=lambda p: p.stem)
def test_poc_mc_matches_oracle_mc(yaml_path):
    """poc.aggregate_mc over the builder index reproduces the oracle's
    aggregate_mc per (class, element, percentile) within a statistical tolerance
    (see the module comment on the kg/kg-vs-authored-unit draw divergence)."""
    sc = SupplyChain.from_yaml(yaml_path)
    ref = sc.aggregate_mc()                       # the oracle reference
    mine = poc.aggregate_mc(build_index(sc.to_graph(), sid=sc.id))
    for cls in set(ref) | set(mine):
        ra, ma = ref.get(cls, {}), mine.get(cls, {})
        assert set(ra) == set(ma), f"{sc.id}/{cls}: element set differs"
        for ec in ra:
            for field in ("best", "lo", "hi"):
                assert ma[ec][field] == pytest.approx(ra[ec][field], **MC_TOL), \
                    f"{sc.id}/{cls}/{ec}/{field}: {ma[ec][field]} != {ra[ec][field]}"


def test_core_builder_emits_no_mc_band():
    """The deterministic core pipeline emits NO fq:amountLow/High; only the poc
    pipeline does (the core builder does no Monte-Carlo)."""
    sc = scenarios.ALL["21_multi_instance_mc"]
    core = resolver.resolve_all(sc.to_graph())
    assert not list(core.subjects(FQ.amountLow, None)), \
        "core builder served graph must carry NO MC band"
    banded = poc.resolve_all_mc(sc.to_graph())
    assert list(banded.subjects(FQ.amountLow, None)), \
        "poc pipeline must add the MC band"


def test_builder_does_not_import_poc():
    """The dependency arrow is poc -> builder only: no module under src/builder
    imports poc (and neither do etl/common/oracle)."""
    import subprocess
    import sys
    root = pathlib.Path(__file__).resolve().parent.parent / "src"
    offenders = []
    for pkg in ("builder", "etl", "common", "oracle"):
        for f in (root / pkg).rglob("*.py"):
            txt = f.read_text()
            if "import poc" in txt or "from poc" in txt:
                offenders.append(str(f.relative_to(root)))
    assert not offenders, f"poc imported by a lower layer: {offenders}"

# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "pytest"]
# ///
"""The golden-oracle gate: the live oracle must reproduce the frozen fixtures
under tests/expected/<version>/ (scenario YAMLs, oneCar RDF, multi-year CSV).
Regenerate ONLY explicitly: uv run scripts/freeze_oracle.py --regenerate."""
import json
import pathlib
import sys


import pytest

import golden                       # tests-local reference-surface definition

SOURCES = dict(golden.iter_sources())
FIXTURES = sorted(golden.expected_dir().glob("*.json"))
APPROX = dict(rel=golden.REL_TOL, abs=golden.ABS_TOL)


def _assert_same(expected, got, where):
    if isinstance(expected, dict):
        assert set(expected) == set(got), (
            f"{where}: keys differ — missing {set(expected) - set(got)}, "
            f"unexpected {set(got) - set(expected)}")
        for k in expected:
            _assert_same(expected[k], got[k], f"{where}.{k}")
    elif isinstance(expected, float):
        assert got == pytest.approx(expected, **APPROX), \
            f"{where}: {got} != {expected}"
    else:
        assert got == expected, f"{where}: {got} != {expected}"


def test_every_source_has_a_fixture():
    """No silent coverage loss in either direction: every golden source is
    frozen, every fixture still has a live source."""
    fixture_ids = {p.stem for p in FIXTURES}
    assert fixture_ids == set(SOURCES), (
        f"missing fixtures: {set(SOURCES) - fixture_ids}; "
        f"stale fixtures: {fixture_ids - set(SOURCES)} "
        f"(run scripts/freeze_oracle.py --regenerate and review the diff)")


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_oracle_matches_golden(fixture):
    sid = fixture.stem
    if sid not in SOURCES:
        pytest.fail(f"fixture {sid} has no live source")
    got = golden.surface(SOURCES[sid]())
    _assert_same(json.loads(fixture.read_text()), got, sid)


# ---------------------------------------------------------------------------
# The element-cell remainder is OMITTED at the ETL source and re-inferred as
# futuram:unknownElement by the resolver's balance() during fq: projection (not a
# rename). Successor contract: test_transformer.py + the serving/builder gates.
# ---------------------------------------------------------------------------

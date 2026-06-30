"""Detail-query regression: every `query-detail-solutions/*.rq` is run against a
live Fuseki and its result is compared to the SI golden — on BOTH backends.

This is the test form of `query-detail-solutions/verify_domain_queries.py`: it
reuses that module's CASES (the SI-expected values) and `check()` (the
golden-comparison, all arithmetic done in SPARQL), so the goldens live in ONE
place. Each (case × backend) is one parametrized test.

It needs a running endpoint and SKIPS when none is reachable, so a plain
`pytest` without a Fuseki up does not fail. Point it at any Fuseki via
`FUSEKI_BASE` (default = the bench Fuseki at :47040); the released datasets
loaded into a throwaway Fuseki work too.

    FUSEKI_BASE=http://localhost:3099 pytest tests/test_detail_queries.py -v
"""
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

QDS = Path(__file__).resolve().parent.parent / "query-detail-solutions"
sys.path.insert(0, str(QDS))

vdq = pytest.importorskip("verify_domain_queries")

BASE = os.environ.get("FUSEKI_BASE", "http://localhost:47040")
BACKENDS = {"fq": BASE + "/query/sparql",
            "composition": BASE + "/composition/sparql"}


def _endpoint_live(url: str) -> bool:
    """A backend is usable iff it answers a trivial ASK with data present."""
    try:
        res = vdq.run_query(url, "ASK { ?s ?p ?o }")
        return bool(res.get("boolean"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


_LIVE = {be: _endpoint_live(url) for be, url in BACKENDS.items()}


def _params():
    for cid, spec in vdq.CASES.items():
        for be in ("fq", "composition"):
            qpath = QDS / vdq.group_of(cid) / be / f"{cid}.rq"
            yield pytest.param(cid, be, qpath, id=f"{be}:{cid}")


@pytest.mark.parametrize("cid,backend,qpath", list(_params()))
def test_detail_query_matches_si(cid, backend, qpath):
    if not _LIVE.get(backend):
        pytest.skip(f"no live data at {BACKENDS[backend]} "
                    f"(set FUSEKI_BASE to a running Fuseki)")
    assert qpath.exists(), f"missing solution file: {qpath}"

    spec = vdq.CASES[cid]
    res = vdq.run_query(BACKENDS[backend], qpath.read_text())
    rows = res["results"]["bindings"]

    # Each query must return at least one row (no silent empty answer)...
    assert rows, f"{backend}:{cid} returned no rows from {BACKENDS[backend]}"

    # ...and its values must match the SI golden (comparison only; the SPARQL
    # engine produced every number).
    ok, got = vdq.check(spec, rows)
    assert ok, (f"{backend}:{cid} mismatch\n  got: {got}\n  SI : {vdq.si_str(spec)}")

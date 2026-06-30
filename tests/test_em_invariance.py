# /// script
# requires-python = ">=3.9"
# dependencies = ["openpyxl", "pyyaml", "rdflib", "pytest"]
# ///
"""Material->element chemistry is YEAR-INVARIANT — emitted ONCE on year-free nodes,
year-drifting chemistry refused loudly, and export_buckets factors it into one shared
material-element.ttl that re-merges to aggregate identically to the direct pipeline.
"""
import copy
import pathlib
import sys


import pytest

from etl import buckets
from etl import csv_to_rdf as X
from chain_from_doc import to_chain
from etl import elv_csv
from common import pipeline
import served as serving

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV = elv_csv("BEV")
YEARS = {2024, 2025, 2026, 2027}

pytestmark = pytest.mark.skipif(not CSV.exists(),
                                reason="ELV_1980_2050_BEV.csv not present")


@pytest.fixture(scope="module")
def doc():
    return X.transform(CSV, sid="em_test", years=YEARS)


@pytest.fixture(scope="module")
def bucket_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("buckets")
    cat = buckets.export_buckets(CSV, out, width=2,
                                 year_min=min(YEARS), year_max=max(YEARS),
                                 canonicalize=False)
    return out, cat


# ---------------------------------------------------------------------------
# 1. the transform: one m->e statement per material, year-free nodes
# ---------------------------------------------------------------------------

def test_em_emitted_once_on_year_free_nodes(doc):
    lvl = {n: s["level"] for n, s in doc["nodes"].items()}
    em = [s for s in doc["statements"]
          if lvl[s["whole"]] == "Material" and lvl[s["part"]] == "Element"]
    assert em, "expected m->e statements"
    # ONE statement per (material node, element node) — never one per year
    assert len(em) == len({(s["whole"], s["part"]) for s in em})
    # material/element node names carry no production year; products do
    for n, l in lvl.items():
        if l in ("Material", "Element"):
            assert not any(str(y) in n for y in YEARS), \
                f"{l} node {n!r} is year-tagged"
    products = [n for n, l in lvl.items() if l == "Product"]
    assert all(any(str(y) in n for y in YEARS) for n in products)


def test_every_year_instance_reaches_the_shared_chemistry(tmp_path):
    """Each (product, year) instance aggregates THROUGH the shared m->e nodes —
    every derived product year-slice class carries element content. Asserted on the
    BUILDER path: ETL emits base-typed instances, the slicer derives the `_Y` leaves."""
    from builder.index import build_index
    from builder import aggregate as A
    from builder import derive as _derive
    g = X.to_graph(CSV, sid="em_test", years=YEARS, canonicalize=False)
    g = _derive._finalise_store(g)
    import re
    idx = build_index(g, sid="em_test")
    agg = A.aggregate(idx)
    # derived product year-slice classes = FutuRaM code (V0301...) + `_Y<year>`
    # suffix minted from referenceYear; every such slice must reach the shared
    # chemistry, i.e. carry element content in the aggregate.
    prod_slice = re.compile(r"^V0301\d+_Y(\d{4})$")
    prod_slices = [c for c in agg
                   if (m := prod_slice.match(c)) and int(m.group(1)) in YEARS]
    assert len(prod_slices) >= len(YEARS)
    for cls in prod_slices:
        assert agg.get(cls), f"no element aggregate for {cls}"


# ---------------------------------------------------------------------------
# 2. enforcement: year-variant chemistry is refused
# ---------------------------------------------------------------------------

def test_year_variant_chemistry_is_refused(monkeypatch):
    rows = X.read_rows(CSV)
    perturbed = []
    bumped = False
    for r in rows:
        if (not bumped and r.get("parameterCode") == "e-m"
                and X._row_year(r) == 2025 and X.num(r.get("value"))):
            r = dict(r)
            r["value"] = float(r["value"]) * 1.5      # 50% drift in ONE year
            bumped = True
        perturbed.append(r)
    assert bumped
    monkeypatch.setattr(X, "read_rows", lambda path, sheet=None: perturbed)
    with pytest.raises(ValueError, match="year-invariant"):
        X.transform(CSV, sid="bad", years={2024, 2025})


# ---------------------------------------------------------------------------
# 3. the bucket split: shared material-element.ttl + identical aggregates
# ---------------------------------------------------------------------------

def test_buckets_factor_out_the_em_layer(bucket_dir):
    out, cat = bucket_dir
    assert cat["shared"] == [pipeline.EM_SHARED_NAME]
    assert (out / pipeline.EM_SHARED_NAME).exists()
    assert cat["shared_stats"]["n_triples"] > 0
    for b in cat["buckets"]:
        assert b["n_em_shared"] > 0        # chemistry was factored out
        # element classes still recorded per bucket (class routing unchanged)
        assert "Copper" in b["classes"]


def test_bucket_plus_shared_equals_direct_pipeline(bucket_dir):
    from served import _load_composition
    from builder.index import build_index
    from builder import aggregate as A
    from builder import derive as _derive
    out, cat = bucket_dir
    b0 = cat["buckets"][0]
    via_g = _load_composition(out / b0["file"])               # merges sibling
    via_g = _derive._finalise_store(via_g)                     # builder derives slices
    direct = to_chain(CSV, sid="direct", years=set(b0["years"]),
                        canonicalize=False)
    agg_a, agg_b = A.aggregate(build_index(via_g, sid="via")), direct.aggregate()
    assert set(agg_a) == set(agg_b)
    # Shared file canonicalises over the FULL range, a direct window chain over its
    # window only; with source noise inside EM_YEAR_TOL the two means may differ by
    # up to that tolerance — that bound, not bit-equality, is the contract.
    for cls, per_elem in agg_b.items():
        for ec, v in per_elem.items():
            got = agg_a[cls].get(ec, 0.0)
            assert abs(got - v) <= X.EM_YEAR_TOL * abs(v) + 1e-9, \
                f"{cls}/{ec}: bucket+shared {got} != direct {v}"


def test_merge_no_longer_renames(bucket_dir):
    """The em_-prefix rename hack is gone: shared m->e and year buckets carry
    content-hashed statement IRIs globally distinct WITHOUT rename, so a merge fuses
    nothing. Asserts no em_-prefixed IRI and disjoint statement IRIs across files."""
    from rdflib import Graph, RDF
    from oracle.supplychain import FUT

    out, cat = bucket_dir
    shared = Graph().parse(out / pipeline.EM_SHARED_NAME, format="turtle")
    bucket = Graph().parse(out / cat["buckets"][0]["file"], format="turtle")

    # content-addressed stmt_<hash> identity is on the per-edge PartRelation now.
    shared_ids = set(shared.subjects(RDF.type, FUT.PartRelation))
    bucket_ids = set(bucket.subjects(RDF.type, FUT.PartRelation))
    assert shared_ids and bucket_ids, "expected statements in both files"

    # (a) the old rename is gone
    assert not any("#em_" in str(s) or "/em_" in str(s)
                   for s in shared_ids | bucket_ids), \
        "em_-prefixed statement IRI found — rename hack still present"
    # (a') content-addressed scheme is in use
    assert all("stmt_" in str(s) for s in shared_ids | bucket_ids)
    # (b) no fusion: the two files' statement IRIs are disjoint
    assert not (shared_ids & bucket_ids), \
        f"shared em file and bucket fuse on {shared_ids & bucket_ids}"


def test_bucketed_endpoint_serves_element_amounts(bucket_dir):
    out, cat = bucket_dir
    ep = serving.BucketedEndpoint(out / buckets.CATALOG_NAME)
    rows = ep.query(
        """
        PREFIX fq: <https://www.purl.org/futuram/query#>
        PREFIX futuram: <https://www.purl.org/futuram#>
        SELECT ?w ?amt WHERE {
          ?a a fq:Amount ; fq:constituent futuram:Copper ;
             fq:whole ?w ; fq:amount ?amt .
        }""",
        years=min(YEARS))
    assert rows, "no copper amounts served — shared m->e layer not merged?"
    assert all(0.0 < float(r["amt"]) <= 1.0 for r in rows)

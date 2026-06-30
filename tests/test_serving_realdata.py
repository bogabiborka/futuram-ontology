# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "pyshacl", "owlrl", "openpyxl", "pytest"]
# ///
"""Serving the REAL ELV dataset, RDF-direct (no YAML): the pipeline (dataset ->
SupplyChain -> composition RDF -> serve) flows end to end on real ELV CSV/Excel
data and the served fq: graph is well-formed."""
import sys
import pathlib


import pytest
from rdflib import Graph, Namespace, RDF

from etl import csv_to_rdf as X
from chain_from_doc import to_chain
from etl import elv_csv, EXAMPLE_XLSX
from common import pipeline
import served as serving
from builder.index import build_index
from builder import aggregate as A
from oracle.supplychain import FUT

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV = elv_csv("BEV")
XLSX = EXAMPLE_XLSX
FQ = Namespace("https://www.purl.org/futuram/query#")
YEARS = {2025}          # a small, fast slice


def _require(path):
    if not path.exists():
        pytest.skip(f"{path.name} not present")


# ---------------------------------------------------------------------------
# Excel (one car) — smaller, faster; exercises the same RDF-direct path.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def xlsx_chain():
    _require(XLSX)
    return to_chain(XLSX, sid="onecar_rdf")


def test_xlsx_rdf_direct_builds_chain(xlsx_chain):
    """oneCarOnly.xlsx -> SupplyChain with no YAML, and it aggregates."""
    agg = xlsx_chain.aggregate()
    assert agg, "no class aggregate produced from the Excel"
    assert xlsx_chain._top_instances(), "no product instances"


def test_xlsx_served_graph_conforms(xlsx_chain):
    """The served fq: graph for the real Excel data is well-formed."""
    g = serving.Endpoint(xlsx_chain).served_graph()
    rep = pipeline.validate_served(g)
    assert rep.conforms, f"served graph violates fq: shapes: {rep.messages[:5]}"
    assert list(g.subjects(RDF.type, FQ.Amount)), "no served amount nodes"


def test_xlsx_to_graph_then_builder_reads_roundtrips(xlsx_chain):
    """RDF emitter + builder reader round-trip on real Excel data: the builder's
    aggregate over build_index(rdf) matches the oracle's."""
    rdf = xlsx_chain.to_graph(full_metadata=True)
    a0, a1 = xlsx_chain.aggregate(), A.aggregate(build_index(rdf, sid="onecar_rt"))
    assert set(a0) == set(a1)
    for cls in a0:
        for ec, v in a0[cls].items():
            assert a1[cls][ec] == pytest.approx(v, rel=2e-3, abs=1e-6)


# ---------------------------------------------------------------------------
# Real ELV CSV (a 1-year slice) — the big, flexible dataset.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def csv_chain():
    _require(CSV)
    return to_chain(CSV, sid="elv_rdf", years=YEARS)


def test_csv_rdf_direct_builds_chain(csv_chain):
    """The real ELV CSV -> SupplyChain (no YAML), with multiple product classes."""
    agg = csv_chain.aggregate()
    assert len(agg) >= 2, f"expected several product classes, got {len(agg)}"


def test_csv_served_graph_conforms(csv_chain):
    """The served fq: graph for the real CSV data is well-formed; a query for an
    element in a product returns a number."""
    ep = serving.Endpoint(csv_chain)
    g = ep.served_graph()
    rep = pipeline.validate_served(g)
    assert rep.conforms, f"served CSV graph violates fq: shapes: {rep.messages[:5]}"
    # pick any class+element the oracle produced and query it through fq:
    agg = csv_chain.aggregate()
    cls = next(iter(agg))
    ec = next(iter(agg[cls]))
    rows = ep.query(f"""
        PREFIX futuram: <{FUT}>
        PREFIX fq: <https://www.purl.org/futuram/query#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?v WHERE {{ futuram:{cls} fq:contains ?a .
            ?a fq:constituent futuram:{ec} ; fq:amount ?v .
            futuram:{ec} rdfs:subClassOf futuram:Element . }}""")
    assert rows and float(list(rows)[0]["v"]) == pytest.approx(agg[cls][ec], rel=2e-3, abs=1e-6)


def test_csv_to_graph_saves_and_reloads(csv_chain, tmp_path):
    """Persisting the composition RDF and reloading it round-trips (the 'save the
    RDF' path)."""
    out = tmp_path / "elv.ttl"
    csv_chain.to_graph(full_metadata=True).serialize(destination=str(out), format="turtle")
    assert out.exists() and out.stat().st_size > 0
    g = Graph(); g.parse(out, format="turtle")
    assert set(A.aggregate(build_index(g, sid="elv_reload"))) == set(csv_chain.aggregate())


def test_served_graph_saves(csv_chain, tmp_path):
    """The served fq: query graph can be persisted to disk (Endpoint.save)."""
    out = tmp_path / "elv_served.ttl"
    serving.Endpoint(csv_chain).save(out)
    assert out.exists() and out.stat().st_size > 0
    g = Graph(); g.parse(out, format="turtle")
    assert list(g.subjects(RDF.type, FQ.Amount)), "saved served graph has no amounts"

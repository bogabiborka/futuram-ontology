# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl", "pytest"]
# ///
"""composition-graph round-trip — RDF is the single interchange format. For every
scenario the builder's aggregate over build_index(sc.to_graph()) equals the oracle's
sc.aggregate() (independent reimplementation), so the fq: projection reads RDF lossless.
"""
import sys
import pathlib


import pytest

import scenarios
from builder.index import build_index
from builder import aggregate as A
import served
Endpoint = served.Endpoint
from oracle.supplychain import FUT

ALL_SIDS = sorted(scenarios.ALL)
APPROX = dict(rel=2e-3, abs=1e-6)
PREFIX = f"""
PREFIX futuram: <{FUT}>
PREFIX fq: <https://www.purl.org/futuram/query#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""


@pytest.mark.parametrize("sid", ALL_SIDS)
def test_roundtrip_aggregate_identical(sid):
    """aggregate(build_index(sc.to_graph())) == sc.aggregate() for every class and
    element — the builder reads composition RDF losslessly into the same values."""
    sc = scenarios.ALL[sid]
    idx = build_index(sc.to_graph(), sid=f"{sid}_rt")
    a0, a1 = sc.aggregate(), A.aggregate(idx)
    assert set(a0) == set(a1), f"{sid}: class set differs {set(a0)} vs {set(a1)}"
    for cls in a0:
        assert set(a0[cls]) == set(a1[cls]), f"{sid}/{cls}: element set differs"
        for ec, v in a0[cls].items():
            assert a1[cls][ec] == pytest.approx(v, **APPROX), \
                f"{sid}/{cls}/{ec}: {a1[cls][ec]} != {v}"


@pytest.mark.parametrize("sid", ALL_SIDS)
def test_roundtrip_structure_preserved(sid):
    """Node count and statement count survive the round-trip (structural
    fidelity, not just the aggregate)."""
    sc = scenarios.ALL[sid]
    idx = build_index(sc.to_graph(), sid=f"{sid}_rt")
    n_nodes = len(idx.levels)
    n_stmts = sum(len(e) for e in idx.adj.values()) + len(idx.coarse)
    assert n_nodes == len(sc.nodes), f"{sid}: node count {n_nodes} != {len(sc.nodes)}"
    assert n_stmts == len(sc.stmts), f"{sid}: stmt count {n_stmts} != {len(sc.stmts)}"


def test_serving_from_reconstructed_graph_matches():
    """Projecting fq: from the reconstructed composition RDF gives the same answer
    as from the original chain — the projection is source-agnostic (RDF in -> RDF out)."""
    sc = scenarios.ALL["24_multi_class"]
    q = PREFIX + """SELECT ?v WHERE { futuram:elvBEV fq:contains ?a .
        ?a fq:constituent futuram:Copper ; fq:amount ?v .
        futuram:Copper rdfs:subClassOf futuram:Element . }"""
    v_orig = float(list(Endpoint(sc).query(q))[0]["v"])
    v_rt = float(list(Endpoint(sc.to_graph()).query(q))[0]["v"])
    assert v_orig == pytest.approx(v_rt, **APPROX)

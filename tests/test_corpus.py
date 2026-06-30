# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl", "pytest"]
# ///
"""The corpus: digest a directory of composition RDF (build_corpus), route queries
to the right files (CorpusRouter.files_for), serve == frozen oracle (CorpusEndpoint),
and self-heal when a file is dropped in. Builds a tiny tmp corpus from a few scenarios.
"""
import sys
import pathlib


import pytest

import scenarios
from etl import corpus
import served as serving
from oracle.supplychain import FUT

PREFIX = f"""
PREFIX futuram: <{FUT}>
PREFIX fq: <https://www.purl.org/futuram/query#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""
APPROX = dict(rel=2e-3, abs=1e-6)
SAMPLE = ["24_multi_class", "20b_multi_instance_unknowns", "22_material_family"]


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory):
    """A small corpus: one composition-RDF file per sample scenario, digested."""
    d = tmp_path_factory.mktemp("corpus")
    for sid in SAMPLE:
        scenarios.ALL[sid].to_graph(full_metadata=True).serialize(
            destination=str(d / f"scenario-{sid}.ttl"), format="turtle")
    corpus.build_corpus(d)
    return d


def test_digest_catalogs_all_files(corpus_dir):
    cat = corpus.build_corpus(corpus_dir)
    ok = [e for e in cat["files"] if "error" not in e]
    assert len(ok) == len(SAMPLE)
    # every entry records its classes + a content signature
    for e in ok:
        assert e["classes"] and "sig" in e and e["sig"]["sha1"]


def test_routing_by_class_prunes_files(corpus_dir):
    """A query for a class loads only files that cover it. Routing by a BASE class
    (elvBEV) finds EVERY file carrying one of its slices, so pruning is asserted on
    a class unique to one file (V0301030101_Y2020 exists only in 24_multi_class)."""
    router = corpus.CorpusRouter(corpus_dir / corpus.CATALOG_NAME)
    routed = router.files_for(classes=["V0301030101_Y2020"])
    assert len(routed) == 1
    assert "24_multi_class" in routed[0].name
    # base-class routing covers all slice-carrying files (no silent pruning)
    base_routed = router.files_for(classes=["elvBEV"])
    assert {p.name for p in base_routed} >= {routed[0].name}
    assert len(base_routed) == 2


def test_corpus_query_matches_oracle(corpus_dir):
    """A routed CorpusEndpoint query returns the frozen oracle's value."""
    ep = serving.CorpusEndpoint(corpus_dir / corpus.CATALOG_NAME)
    sc = scenarios.ALL["24_multi_class"]
    exp = sc.aggregate()["elvBEV"]["Copper"]
    # route by a class unique to scenario 24's file so exactly that file is
    # loaded; the year-sliced parent elvBEV_Y2020 then equals the scenario
    # oracle's elvBEV (one year -> the base IS its slice's year mean).
    rows = ep.query(PREFIX + """
        SELECT ?v WHERE { futuram:elvBEV_Y2020 fq:contains ?a .
            ?a fq:constituent futuram:Copper ;
               fq:whole futuram:elvBEV_Y2020 ; fq:amount ?v .
            futuram:Copper rdfs:subClassOf futuram:Element . }""",
        classes=["V0301030101_Y2020"])
    assert rows, "no answer routed for elvBEV_Y2020 copper"
    assert float(rows[0]["v"]) == pytest.approx(exp, **APPROX)


def test_drop_in_new_file_is_picked_up(corpus_dir):
    """Dropping a new composition-RDF file in and constructing a fresh endpoint
    picks it up with NO manual digest step (self-healing / git-friendly)."""
    before = len(corpus.CorpusRouter(corpus_dir / corpus.CATALOG_NAME).files)
    # drop in a scenario not already present
    new_sid = "25_deep_four_car"
    scenarios.ALL[new_sid].to_graph(full_metadata=True).serialize(
        destination=str(corpus_dir / f"scenario-{new_sid}.ttl"), format="turtle")
    after = len(corpus.CorpusRouter(corpus_dir / corpus.CATALOG_NAME).files)
    assert after == before + 1, "auto-digest did not pick up the dropped-in file"
    # and the new file is immediately queryable
    ep = serving.CorpusEndpoint(corpus_dir / corpus.CATALOG_NAME)
    sc = scenarios.ALL[new_sid]
    cls = "elvBEV"
    exp = sc.aggregate()[cls]["Copper"]
    rows = ep.query(PREFIX + f"""
        SELECT ?v WHERE {{ futuram:{cls} fq:contains ?a .
            ?a fq:constituent futuram:Copper ;
               fq:whole futuram:{cls} ; fq:amount ?v .
            futuram:Copper rdfs:subClassOf futuram:Element . }}""", classes=[cls])
    assert any(float(r["v"]) == pytest.approx(exp, **APPROX) for r in rows)


def test_incremental_digest_reuses_unchanged(corpus_dir):
    """Re-digesting without changes reuses entries by signature (cheap)."""
    cat1 = corpus.build_corpus(corpus_dir)
    cat2 = corpus.build_corpus(corpus_dir)
    sigs1 = {e["path"]: e["sig"]["sha1"] for e in cat1["files"] if "sig" in e}
    sigs2 = {e["path"]: e["sig"]["sha1"] for e in cat2["files"] if "sig" in e}
    assert sigs1 == sigs2

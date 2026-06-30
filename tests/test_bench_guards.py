"""Unit tests for the bench pre-execution SPARQL guards (bench/helpers/*).

The guards turn a fabricated class/predicate or an opaque-IRI string-match into a
HARD, named error BEFORE the query runs — instead of the silent zero-rows SPARQL
returns for an unknown IRI. These tests pin the behaviour that regressed in real
runs:

  * predicatecheck must NOT mis-read a property-path OBJECT (e.g. `crit:CRITICAL`
    in `crit:remark/crit:importance crit:CRITICAL`) as a predicate (false reject of
    a VALID criticality query).
  * iriguard must BLOCK concept-word fishing on a class IRI (`CONTAINS(STR(?c),
    "copper")`) but ALLOW a namespace/scheme test (`CONTAINS(STR(?chebi),"CHEBI")`)
    — and recognise the class role whether owl:Class/rdf:type/subClassOf are written
    prefixed or as full <IRI>s.
  * classcheck is DATA-backed: a real class absent from the (incomplete) VoID but
    present in the data is ALLOWED; only a class provably absent from the data is
    blocked. The rule is "when in doubt, allow".

No network: the tokeniser / role-detection are pure, and the data-existence probe
is monkeypatched.
"""
import importlib
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
BENCH = REPO / "bench"
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

rdflib = pytest.importorskip("rdflib")  # helpers import rdflib at module load

predicatecheck = importlib.import_module("helpers.predicatecheck")
iriguard = importlib.import_module("helpers.iriguard")
classcheck = importlib.import_module("helpers.classcheck")
valuesguard = importlib.import_module("helpers.valuesguard")


def test_valuesguard_blocks_handlisted_set():
    # 3+ enumerated domain class IRIs in VALUES / IN -> blocked
    vals = ("SELECT ?s WHERE { VALUES ?s { futuram:A futuram:B futuram:C } "
            "?s fq:contains ?x }")
    assert valuesguard.check(vals, None), "3-IRI VALUES must be blocked"
    inlist = ("SELECT ?a WHERE { ?n fq:constituent ?c ; fq:amount ?a . "
              "FILTER(?c IN (futuram:A, futuram:B, futuram:C)) }")
    assert valuesguard.check(inlist, None), "IN() of 3 must be blocked"


def test_valuesguard_allows_legit_queries():
    # single subject is fine
    one = ("SELECT ?a WHERE { futuram:X fq:contains "
           "[ fq:constituent futuram:Cu ; fq:amount ?a ] }")
    assert valuesguard.check(one, None) is None
    # the CORRECT discovery query (subClassOf, no enumerated set) is fine
    disc = ("SELECT ?sub ?kg WHERE { ?sub rdfs:subClassOf futuram:Parent ; "
            "fq:itemMass ?m ; fq:contains [ fq:constituent futuram:Cu ; fq:amount ?f ] }")
    assert valuesguard.check(disc, None) is None
    # a small 2-IRI VALUES pair is allowed (below the threshold)
    pair = "SELECT ?x WHERE { VALUES ?x { futuram:A futuram:B } ?x fq:contains ?c }"
    assert valuesguard.check(pair, None) is None


# --------------------------------------------------------------------------- #
# predicatecheck._predicate_tokens — only the VERB slot, paths split, objects out
# --------------------------------------------------------------------------- #
def test_predicate_tokens_excludes_path_object():
    # the criticality path: remark + importance are predicates; CRITICAL is the
    # OBJECT and must NOT be read as a predicate (that false-rejected valid queries).
    q = ("WHERE { ?c rdfs:subClassOf ?chebi . "
         "?chebi crit:remark/crit:importance crit:CRITICAL }")
    toks = predicatecheck._predicate_tokens(q)
    assert "crit:remark" in toks
    assert "crit:importance" in toks
    assert "crit:CRITICAL" not in toks       # the path OBJECT — not a predicate
    assert "rdfs:subClassOf" in toks


def test_predicate_tokens_blank_node_list_and_select_noise():
    q = ("SELECT (SUM(?a*?m) AS ?kg) WHERE { "
         "?v fq:itemMass ?m ; fq:contains [ fq:constituent ?e ; fq:amount ?a ] }")
    toks = predicatecheck._predicate_tokens(q)
    assert toks == {"fq:itemMass", "fq:contains", "fq:constituent", "fq:amount"}


def test_predicate_tokens_subclassof_star_path():
    q = "WHERE { ?x rdfs:subClassOf* <http://purl.obolibrary.org/obo/CHEBI_33319> }"
    assert predicatecheck._predicate_tokens(q) == {"rdfs:subClassOf"}


# --------------------------------------------------------------------------- #
# iriguard — concept-fishing blocked, namespace test allowed, role by full-IRI too
# --------------------------------------------------------------------------- #
def test_iriguard_blocks_concept_fishing_on_class():
    for typing in ("owl:Class", "<http://www.w3.org/2002/07/owl#Class>"):
        q = (f"SELECT ?c WHERE {{ ?c a {typing} . "
             f"FILTER(CONTAINS(LCASE(STR(?c)),'copper')) }}")
        assert iriguard.check(q, None), f"should block concept-fishing ({typing})"


def test_iriguard_allows_namespace_test_on_class():
    # matching the CHEBI / obo namespace fragment is a vocabulary test, not fishing
    for needle in ("'CHEBI'", "'http://purl.obolibrary.org/obo/'"):
        q = (f"SELECT ?c WHERE {{ ?c rdfs:subClassOf ?chebi . "
             f"FILTER(CONTAINS(STR(?chebi),{needle})) ?chebi a owl:Class }}")
        assert iriguard.check(q, None) is None, f"should allow namespace test {needle}"


def test_iriguard_ignores_str_on_non_class_var():
    # STR() on a LABEL literal is fine — only a CLASS-role var is guarded
    q = ("SELECT ?c WHERE { ?c a owl:Class ; rdfs:label ?l . "
         "FILTER(CONTAINS(LCASE(STR(?l)),'copper')) }")
    assert iriguard.check(q, None) is None


def test_namespace_token_classifier():
    assert iriguard._is_namespace_token("CHEBI")
    assert iriguard._is_namespace_token("CHEBI_33319")
    assert iriguard._is_namespace_token("http://purl.obolibrary.org/obo/")
    assert not iriguard._is_namespace_token("copper")
    assert not iriguard._is_namespace_token("critical raw material")


# --------------------------------------------------------------------------- #
# classcheck — DATA-backed: real-but-VoID-absent allowed, invented blocked
# --------------------------------------------------------------------------- #
class _FakeInv:
    """Minimal inventory so classcheck.check runs without reading a real VoID."""
    @staticmethod
    def install(monkeypatch, exists: set):
        monkeypatch.setattr(classcheck, "_inventory",
                            lambda ep: {"iris": {"https://www.purl.org/futuram#Material"},
                                        "by_local": {}, "by_label": {}})
        # the data-existence ASK is the authority — fake it from `exists`
        monkeypatch.setattr(classcheck, "_exists_in_data",
                            lambda ep, iri: iri in exists)
        # don't hit the live "did you mean" enumerator
        monkeypatch.setattr(classcheck, "_live_candidates",
                            lambda *a, **k: [])


def test_classcheck_allows_real_class_absent_from_void(monkeypatch):
    # CHEBI_33319 is NOT in the VoID inventory but IS in the data -> ALLOW
    real = "http://purl.obolibrary.org/obo/CHEBI_33319"
    _FakeInv.install(monkeypatch, exists={real})
    q = (f"PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#> "
         f"SELECT ?x WHERE {{ ?x rdfs:subClassOf* <{real}> }}")
    assert classcheck.check(q, "ep") is None


def test_classcheck_blocks_invented_class(monkeypatch):
    # crit:CriticalRawMaterial is in neither VoID nor data -> BLOCK
    _FakeInv.install(monkeypatch, exists=set())
    q = ("PREFIX crit:<http://purl.org/futuram/criticality#> "
         "PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#> "
         "SELECT ?x WHERE { ?x rdfs:subClassOf* crit:CriticalRawMaterial }")
    msg = classcheck.check(q, "ep")
    assert msg and "CriticalRawMaterial" in msg

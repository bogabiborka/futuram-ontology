# /// script
# requires-python = ">=3.9"
# dependencies = ["openpyxl", "pyyaml", "rdflib", "pytest"]
# ///
"""canonicalize_doc is DATA-DRIVEN and LOSSLESS on real data: keeps every measured
element, gives each material class ONE composition, and splits a class differing
BY COMPONENT into context-named subclasses (each rdfs:subClassOf its parent).
"""
import copy
import pathlib
import sys


import pytest

from etl import chain_loader
import chain_from_doc as _cfd
from etl import elv_csv
from etl import csv_to_rdf as X

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSV = elv_csv("BEV")

pytestmark = pytest.mark.skipif(not CSV.exists(),
                                reason="ELV_1980_2050_BEV.csv not present")


@pytest.fixture(scope="module")
def raw_and_canon():
    doc = X.transform(CSV, sid="canon_test", years={2025})
    raw = _cfd.chain_from_doc(copy.deepcopy(doc))
    canon_doc, changes = chain_loader.canonicalize_doc(copy.deepcopy(doc))
    canon = _cfd.chain_from_doc(canon_doc)
    return raw, canon, canon_doc, changes


def test_no_element_is_dropped_from_the_aggregate(raw_and_canon):
    """Every element present in the raw aggregate is still present after
    canonicalisation — including the rare earths and precious metals the old
    archetype snap erased (Nd, Dy, Pr, Au, Ag, Pd, Co, Mg, Si, Mn, ...)."""
    raw, canon, _, _ = raw_and_canon
    a = raw.aggregate()["elvBEV"]
    b = canon.aggregate()["elvBEV"]
    gone = sorted(e for e, v in a.items() if v > 1e-9 and b.get(e, 0.0) < 1e-12)
    assert not gone, f"canonicalisation erased elements: {gone}"
    # the specific criticals FutuRaM exists to track must survive with content
    for crit in ("Neodymium", "Dysprosium", "Gold", "Silver", "Palladium"):
        assert b.get(crit, 0.0) > 0.0, f"{crit} lost"


def test_canonicalisation_is_close_to_measured(raw_and_canon):
    """Canonicalisation only smooths product/year noise — the aggregate barely
    moves (no element shifts by more than a few parts in 1e3)."""
    raw, canon, _, _ = raw_and_canon
    a = raw.aggregate()["elvBEV"]
    b = canon.aggregate()["elvBEV"]
    for e in set(a) | set(b):
        assert abs(a.get(e, 0.0) - b.get(e, 0.0)) < 5e-3, \
            f"{e}: {a.get(e,0)} -> {b.get(e,0)} moved too far"


def test_every_material_class_is_consistent(raw_and_canon):
    """The point of canonicalisation: one intrinsic composition per material
    class (a material node with no measured elements inherits its class's)."""
    _, canon, _, _ = raw_and_canon
    assert canon.check_material_consistency() is True
    assert canon.check_fanout() is True
    over = {w for w, d in canon.conservation().items() if d["overshoot"]}
    assert not over, f"overshoots introduced: {over}"


def test_multi_composition_classes_split_by_component_with_semantic_names(
        raw_and_canon):
    """A material whose make-up differs by component is split into
    context-named subclasses (NOT averaged, NOT numbered), each a subclass of
    the original."""
    _, _, canon_doc, _ = raw_and_canon
    classes = {spec["class"] for spec in canon_doc["nodes"].values()}
    sub = canon_doc.get("subclass_of", {})
    # pureCu differs: 0.99 Cu in the motor, 0.85 in cables -> two named subs
    assert "pureCu_in_elvElectricMotor" in classes
    assert "pureCu_in_elvEmbeddedElectronicsCables" in classes
    # subclass_of values are lists of superclasses (the normalised corpus shape)
    assert sub["pureCu_in_elvElectricMotor"] == ["pureCu"]
    assert sub["pureCu_in_elvEmbeddedElectronicsCables"] == ["pureCu"]
    # the split names are self-describing: no opaque __c1/__c2 numbering
    assert not any("__c" in c for c in classes)
    # the two motor/cable copper fractions stay DISTINCT (not averaged to one)
    canon = _cfd.chain_from_doc(canon_doc)
    comps = canon.material_composition_by_class()
    motor = comps["pureCu_in_elvElectricMotor"][0]["Copper"]
    cable = comps["pureCu_in_elvEmbeddedElectronicsCables"][0]["Copper"]
    assert abs(motor - 0.99) < 1e-6 and abs(cable - 0.85) < 1e-6


def test_single_composition_class_is_not_split(raw_and_canon):
    """A material that sits in only ONE component context keeps its plain class
    name (MgAndMgAlloys is all in elvGeneralComponents -> no split), even though
    some of its nodes lack element data (they inherit, not split)."""
    _, _, canon_doc, _ = raw_and_canon
    classes = {spec["class"] for spec in canon_doc["nodes"].values()}
    assert "MgAndMgAlloys" in classes
    assert not any(c.startswith("MgAndMgAlloys_in_") for c in classes)

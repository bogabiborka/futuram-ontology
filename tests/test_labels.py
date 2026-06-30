# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "owlrl", "pytest"]
# ///
"""LabelPlugin — every served class gets an rdfs:label DERIVED from RDF (never a
per-class map). Regression for the bench gap where a label-less aggregate whole was
unresolvable by label search; assertions are derivation-shape invariants 1-4."""
import pytest
from rdflib import Graph, Namespace, RDF, RDFS, OWL

from etl.composition_rdf import composition_rdf
from builder import derive
from builder.resolver.plugins.labels import _common_prefix, _humanise

FUT = Namespace("https://www.purl.org/futuram#")
FQ = Namespace("https://www.purl.org/futuram/query#")


# ---------------------------------------------------------------------------
# Pure derivation rules — synthetic inputs, no repo data.
# ---------------------------------------------------------------------------
def test_humanise_is_a_pure_string_transform():
    assert _humanise("elvElectricMotor") == "electric motor"
    assert _humanise("elvGeneralComponents") == "general components"
    # the unknown* remainder reads as "unattributed <level>", not a thing named
    # "<level>" (it is the mass not pinned to a named class)
    assert _humanise("unknownElement") == "unattributed element"
    assert _humanise("unknownComponent") == "unattributed component"


def test_common_prefix_is_shared_head_trimmed_at_a_boundary():
    # children that DIVERGE right after the shared head (the real V-code shape:
    # one child "… — standard / …", another "… — segment A …") collapse to the
    # head, trimmed of the dangling separator.
    labels = ["demo drivetrain — standard [V00]",
              "demo drivetrain — segment A [V01]",
              "demo drivetrain — luxury [V06]"]
    assert _common_prefix(labels) == "demo drivetrain"
    assert _common_prefix(["abc"]) == ""           # single label -> no prefix
    assert _common_prefix(["foo", "bar"]) == ""    # nothing shared


# ---------------------------------------------------------------------------
# Builder-level invariants over a synthetic served graph (cached per module).
# ---------------------------------------------------------------------------
_PROV = {"source": "test", "agent": "test", "production": "use",
         "validFrom": "2020-01-01", "validUntil": "2020-12-31"}


_CHILD_LABEL = "demo drivetrain — segment A"   # a source label, code-free


def _doc():
    """A minimal composition doc that projects through the builder: source-labelled
    product VA with a 2020 Iron-part instance, making the builder mint slice VA_Y2020.
    Exercises rules 1, 2 and 4 (rule 3 is the pure _common_prefix test above)."""
    return {"id": "labels_test", "provenance": _PROV,
            "subclass_of": {"VA": ["Product"]},
            "class_labels": {"VA": _CHILD_LABEL},
            "node_time": {"car": {"year": 2020}},
            "nodes": {"car": {"level": "Product", "class": "VA", "itemMass": 1000.0},
                      "fe": {"level": "Element", "class": "Iron"}},
            "statements": [{"whole": "car", "part": "fe",
                            "best": 0.5, "unit": "kgkg", "dist": "rectangular"}]}


@pytest.fixture(scope="module")
def served():
    comp = composition_rdf(_doc())
    merged, _n = derive.merge_sources([("labels_test", comp)])
    return derive.derive_all(merged)


def _classes(g):
    return [s for s in g.subjects(RDF.type, OWL.Class)
            if str(s).startswith(str(FUT))]


def test_no_served_class_is_label_less(served):
    """The invariant the bench relies on: every served class is resolvable by a
    label search (the bench gap was a label-less served class). Covers rule 4:
    the unknown* remainders the resolver fills are labelled too."""
    missing = [str(s) for s in _classes(served)
               if served.value(s, RDFS.label) is None]
    assert missing == [], f"label-less served classes: {missing[:10]}"


def test_no_served_class_is_comment_less(served):
    """Every served class must ALSO carry an rdfs:comment — the bench resolves a
    plain-language term by searching label AND comment, so a comment-less class is
    only half-discoverable. CommentPlugin must cover every class LabelPlugin does."""
    missing = [str(s) for s in _classes(served)
               if served.value(s, RDFS.comment) is None]
    assert missing == [], f"comment-less served classes: {missing[:10]}"


def test_slice_carries_source_base_label_and_names_its_scope(served):
    """Rules 1+2: the minted year slice's label = its base's source label (verbatim)
    + the production-year phrase. Asserts the shape (label contains base, names the
    year), not a pinned full string."""
    slices = [(s, served.value(s, FQ.sliceOf), served.value(s, FQ.referenceYear))
              for s in served.subjects(FQ.sliceOf, None)]
    slices = [(s, b, y) for s, b, y in slices
              if b is not None and y is not None
              and served.value(s, RDFS.label) is not None]
    assert slices, "expected at least one labelled year slice"
    for s, base, yr in slices:
        slab = str(served.value(s, RDFS.label))
        blab = served.value(base, RDFS.label)
        if blab is not None:
            assert slab.startswith(str(blab)), \
                f"slice label {slab!r} not based on {blab!r}"
        assert str(int(yr)) in slab, f"slice label {slab!r} omits year {yr}"
    # and the authored base label is carried verbatim onto the slice head
    va_slice = [str(served.value(s, RDFS.label)) for s, b, _ in slices
                if str(b) == str(FUT.VA)]
    assert va_slice and va_slice[0].startswith(_CHILD_LABEL)


def test_label_never_embeds_the_raw_class_code(served):
    """A product label is the human name only — never the raw V-code (that is the
    class IRI's own local name; repeating it is noise). No served label should wrap
    a code in brackets."""
    import re
    bad = [str(o) for o in served.objects(None, RDFS.label)
           if re.search(r"\[V\d", str(o))]
    assert bad == [], f"labels still embed a raw code: {bad[:5]}"


def test_year_slice_label_is_distinguishable_from_base_for_bench_generator(served):
    """GUARD for segment-class resolution by label (the timeless base is distinguished
    from its year slices via !CONTAINS "production year"/"period"): pins that every
    slice carries one phrase and the base neither — so an LLM can resolve a segment
    question (e.g. SI Q6/Q7) to the base class, not a year slice."""
    _SCOPE_PHRASES = ("production year", "production period")

    def _has_scope_phrase(label):
        low = str(label).lower()
        return any(p in low for p in _SCOPE_PHRASES)

    # (a) every LABELLED year slice names its scope with one of the keyed phrases
    slices = [s for s in served.subjects(FQ.sliceOf, None)
              if served.value(s, FQ.referenceYear) is not None
              and served.value(s, RDFS.label) is not None]
    assert slices, "expected at least one labelled year slice"
    for s in slices:
        lab = served.value(s, RDFS.label)
        assert _has_scope_phrase(lab), (
            f"year-slice label {str(lab)!r} omits the scope phrase the bench "
            f"generator filters on ({_SCOPE_PHRASES}) — base resolution would "
            f"pick up this slice and skip the domain cases")

    # (b) the timeless BASE of each slice carries NEITHER phrase, so the generator's
    # exclusion filter lands on it (and only it).
    bases = {served.value(s, FQ.sliceOf) for s in slices}
    for base in bases:
        blab = served.value(base, RDFS.label)
        if blab is not None:
            assert not _has_scope_phrase(blab), (
                f"timeless base label {str(blab)!r} contains a scope phrase — the "
                f"generator's `!CONTAINS(...production year...)` filter would exclude "
                f"the base too, resolving the segment to nothing")


# ---------------------------------------------------------------------------
# The SHIPPED served artifact — guards the file the bench actually loads from
# going STALE (built without the label/comment plugins). The bench's whole
# class-discovery strategy (search rdfs:label / rdfs:comment, never the opaque
# IRI) is only valid if the SERVED file carries them on every domain class.
# ---------------------------------------------------------------------------
from pathlib import Path

_SERVED_FILE = (Path(__file__).resolve().parents[1]
                / "fuseki" / "futuram" / "data" / "query" / "futuram.ttl")
_ROOTS = ("Product", "Component", "Material", "Element")


@pytest.fixture(scope="module")
def shipped():
    if not _SERVED_FILE.exists():
        pytest.skip(f"served file not built: {_SERVED_FILE}")
    g = Graph()
    g.parse(str(_SERVED_FILE))
    return g


def _domain_subclasses(g):
    """Every futuram class that is a subclass (transitively) of one of the four
    kinds Product/Component/Material/Element — the classes a question is ABOUT.
    Excludes the roots themselves, ChEBI/external classes, and schema vocabulary."""
    out = set()
    for root in _ROOTS:
        for s in g.transitive_subjects(RDFS.subClassOf, FUT[root]):
            if s != FUT[root] and str(s).startswith(str(FUT)):
                out.add(s)
    return out


def test_shipped_served_domain_classes_all_have_label(shipped):
    """Every Product/Component/Material/Element subclass in the SHIPPED served file
    has an rdfs:label — so the bench can resolve any term by label search. A failure
    means the served artifact is STALE (rebuild: tests/build_instances.py futuram)."""
    subs = _domain_subclasses(shipped)
    assert subs, "no domain subclasses found — served file unexpectedly empty/shaped"
    missing = sorted(str(s) for s in subs if shipped.value(s, RDFS.label) is None)
    assert missing == [], (
        f"{len(missing)} served domain classes have NO rdfs:label "
        f"(served file is stale — rebuild it): {missing[:10]}")


def test_shipped_served_domain_classes_all_have_comment(shipped):
    """Every Product/Component/Material/Element subclass in the SHIPPED served file
    has an rdfs:comment — the bench searches label AND comment to resolve a class."""
    subs = _domain_subclasses(shipped)
    assert subs, "no domain subclasses found — served file unexpectedly empty/shaped"
    missing = sorted(str(s) for s in subs if shipped.value(s, RDFS.comment) is None)
    assert missing == [], (
        f"{len(missing)} served domain classes have NO rdfs:comment "
        f"(served file is stale — rebuild it): {missing[:10]}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "pytest", "owlrl"]
# ///
"""Shared test harness for the composition-statement pipeline.

Helpers load TBox + hierarchy + a fixture, run core SHACL and/or a SPARQL rule,
and expose the result. Fixtures live in tests/fixtures/ as tiny Turtle files.
"""
import pytest

# RDF primitives + path constants live in common.pipeline (test-free library);
# conftest RE-EXPORTS them under the same names (H.TBOX, H.run_rule, ...).
from common.pipeline import (  # noqa: F401  (re-exported for tests)
    ROOT,
    FIXTURES,
    TBOX,
    SHAPES,
    HIERARCHY,
    RULE_CONSERVATION,
    RULE_LIFT,
    RULE_COMPLETE_CHAINS,
    RULE_PROPAGATE_GRANULAR,
    RULE_RECONCILE,
    load_graph as load,
    rdfs_closure,
    run_rule,
    run_rule_fixpoint,
    shacl,
)


def validate_fixture(name, run_conservation=False):
    """Convenience: load fixture, optionally run the conservation rule, SHACL it."""
    g = load(name)
    if run_conservation:
        run_rule(g, RULE_CONSERVATION)
    return shacl(g)


@pytest.fixture
def load_fixture():
    return load

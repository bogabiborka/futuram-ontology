# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "owlrl"]
# ///
"""RDF-processing primitives + pipeline operations (rdflib/pyshacl/owlrl only, no
pytest) so the fq: resolver can reuse them; conftest re-exports for tests. Ops:
build_graph, validate (SHACL gate), materialize (four CONSTRUCT rules), reconcile.
"""
import pathlib
from collections import namedtuple

from rdflib import Graph

# ---------------------------------------------------------------------------
# Paths — frozen TBox/shapes/hierarchy + CONSTRUCT rules (conftest re-exports).
# ---------------------------------------------------------------------------
def _repo_root(start=None):
    """The repository root — the nearest ancestor containing pyproject.toml.
    Anchored on a stable marker so it survives the module moving between package
    depths (never count .parent levels)."""
    p = (start or pathlib.Path(__file__)).resolve()
    for cand in (p, *p.parents):
        if (cand / "pyproject.toml").exists():
            return cand
    return pathlib.Path(__file__).resolve().parent.parent.parent


# repo root (shared anchor; other modules import this rather than recomputing).
ROOT = _repo_root()

TBOX = ROOT / "ontology" / "tbox" / "composition-statement.ttl"
SHAPES = ROOT / "shapes" / "composition-statement-shapes.ttl"
# itemMass (absolute-kg reference anchor) instance shapes — core SHACL, loaded
# alongside SHAPES so the mandatory-on-Product/Component rule runs on every graph.
ITEM_MASS_SHAPES = ROOT / "shapes" / "item-mass-shapes.ttl"
HIERARCHY = ROOT / "ontology" / "tbox" / "futuram-hierarchy.ttl"

# The query-optimized fq: ontology (LLM-facing flat view) + its SHACL shapes.
QUERY_TBOX = ROOT / "ontology" / "tbox" / "composition-query.ttl"
QUERY_SHAPES = ROOT / "shapes" / "composition-query-shapes.ttl"

# The data-quality -> uncertainty rule as RDF (TBox shape + FutuRaM DQS ABox).
# Loaded into the resolver's TBox (so the uncertainty plugin applies it) and merged
# into the served graph (so the rule travels with the data it explains).
UNCERTAINTY_TBOX = ROOT / "ontology" / "tbox" / "uncertainty-ruleset.ttl"

# Combined-graph rules of the time-based-classes model (S1-S5): time scopes on
# Product/Component classes + statement-or-strategy validity. Run over hierarchy
# ABox + composition ABox together (each alone may be valid).
TIME_SHAPES = ROOT / "shapes" / "time-strategy-shapes.ttl"

# The shared, YEAR-INVARIANT material->element layer a bucket export factors out of
# its year-window files. A bucket .ttl is complete only with the sibling of this
# name; from_graph.from_turtle merges it back automatically.
EM_SHARED_NAME = "material-element.ttl"

# Reserved artefact filenames that are NOT composition sources (TBox, shapes,
# hierarchy, m->e sibling); corpus scans skip these. Lives in common so ETL and
# builder share the convention without the builder depending on etl.
SKIP_NAMES = {"composition-statement.ttl", "composition-statement-shapes.ttl",
              "composition-query.ttl", "composition-query-shapes.ttl",
              "futuram-hierarchy.ttl",
              EM_SHARED_NAME}

RULE_CONSERVATION = ROOT / "rules" / "check-mass-conservation.rq"
RULE_LIFT = ROOT / "rules" / "infer-class-composition.rq"
RULE_COMPLETE_CHAINS = ROOT / "rules" / "complete-chains.rq"
RULE_PROPAGATE_GRANULAR = ROOT / "rules" / "propagate-granular.rq"
RULE_RECONCILE = ROOT / "rules" / "reconcile-coarse-fine.rq"

FIXTURES = ROOT / "tests" / "fixtures"


# A validation verdict: did core SHACL conform, and the human-readable messages.
Report = namedtuple("Report", ["conforms", "messages"])


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------
def load_graph(*paths, with_tbox=True, with_hierarchy=True):
    """Parse a fixture (by name or path) plus optionally the TBox and hierarchy."""
    g = Graph()
    if with_tbox:
        g.parse(TBOX, format="turtle")
    if with_hierarchy:
        g.parse(HIERARCHY, format="turtle")
    for p in paths:
        p = pathlib.Path(p)
        if not p.is_absolute():
            p = FIXTURES / p
        g.parse(p, format="turtle")
    return g


def rdfs_closure(graph):
    """Materialise the RDFS closure in place and return the graph. The level types
    (Component, Material, ...) the rules key on exist only via the subClassOf chain;
    closing RDFS makes `?x a futuram:Component` resolvable (mirrors inference='rdfs')."""
    import owlrl
    owlrl.DeductiveClosure(owlrl.RDFS_Semantics).expand(graph)
    return graph


def run_rule(graph, rule_path):
    """Run a SPARQL CONSTRUCT rule and union its output into the graph (mutates).
    Pure CONSTRUCT — no inference; a rule depending on inferred level types must be
    handed an already-RDFS-closed graph (see rdfs_closure)."""
    q = pathlib.Path(rule_path).read_text()
    for triple in graph.query(q):
        graph.add(triple)
    return graph


def run_rule_fixpoint(graph, rule_path, max_iter=64):
    """Run a CONSTRUCT rule REPEATEDLY until the graph stops growing.
    A transitive rule extends paths one edge per pass; iterating to a fixpoint
    accumulates arbitrary-length paths. Stops when no new triples, or after max_iter."""
    q = pathlib.Path(rule_path).read_text()
    for _ in range(max_iter):
        before = len(graph)
        for triple in graph.query(q):
            graph.add(triple)
        if len(graph) == before:
            break
    return graph


def shacl(graph):
    """Validate with core SHACL. Returns (conforms, messages list)."""
    from pyshacl import validate as _shacl_validate
    shapes = Graph()
    shapes.parse(SHAPES, format="turtle")
    shapes.parse(ITEM_MASS_SHAPES, format="turtle")
    conforms, _report_graph, report_text = _shacl_validate(
        graph, shacl_graph=shapes, inference="rdfs", advanced=False
    )
    msgs = [
        line.split("Message:", 1)[1].strip()
        for line in report_text.splitlines()
        if "Message:" in line
    ]
    return conforms, msgs


# ---------------------------------------------------------------------------
# Pipeline operations
# ---------------------------------------------------------------------------
def build_graph(supplychain, *, full_metadata=True, rdfs=False):
    """TBox + hierarchy + the supply chain's emitted instance graph.
    Optionally RDFS-close so the futuram level types (Component/Material/...) the
    rules key on are materialised."""
    g = Graph()
    g.parse(TBOX, format="turtle")
    g.parse(HIERARCHY, format="turtle")
    g += supplychain.to_graph(full_metadata=full_metadata)
    if rdfs:
        rdfs_closure(g)
    return g


def validate(graph):
    """The GATE: RDFS-close a COPY, run core SHACL, return a Report.
    A non-conforming graph must not be aggregated. Validates a copy so the caller's
    graph is unmutated (validation is side-effect free; materialize() mutates)."""
    g = Graph()
    for t in graph:
        g.add(t)
    rdfs_closure(g)
    conforms, msgs = shacl(g)
    return Report(conforms=conforms, messages=msgs)


def materialize(graph, *, rdfs=True):
    """Run the four CONSTRUCT rules in spec order, unioning each into `graph` (mutates,
    returns); rdfs=True RDFS-closes first. ORDER: complete-chains (inserts adjacent
    unknown* fillers) BEFORE the STRICT lift, which turns them into class edges."""
    if rdfs:
        rdfs_closure(graph)
    run_rule(graph, str(RULE_COMPLETE_CHAINS))   # insert fillers FIRST …
    run_rule(graph, RULE_LIFT)                   # … so the STRICT lift sees them
    run_rule_fixpoint(graph, str(RULE_PROPAGATE_GRANULAR))
    run_rule(graph, str(RULE_RECONCILE))
    run_rule(graph, RULE_CONSERVATION)
    return graph


def validate_time_strategy(graph, *, with_hierarchy=True):
    """Validate the COMBINED two-ABox graph against the time-based-classes rules
    (S1-S5, shapes/time-strategy-shapes.ttl). inference='none' (shapes use explicit
    subClassOf* paths); TBox+hierarchy parsed alongside unless already present."""
    from pyshacl import validate as _shacl_validate
    data = Graph()
    for t in graph:
        data.add(t)
    data.parse(TBOX, format="turtle")
    if with_hierarchy:
        data.parse(HIERARCHY, format="turtle")
    shapes = Graph()
    shapes.parse(TIME_SHAPES, format="turtle")
    conforms, _rg, report_text = _shacl_validate(
        data, shacl_graph=shapes, inference="none", advanced=False
    )
    msgs = [
        line.split("Message:", 1)[1].strip()
        for line in report_text.splitlines()
        if "Message:" in line
    ]
    return Report(conforms=conforms, messages=msgs)


def validate_served(graph):
    """Validate a projected fq: graph against the fq: shapes; returns a Report.
    inference='none' on PURPOSE: RDFS-closing the flat graph would inject owl:Class
    types defeating the class-only fq:contains guard (§3). Served TBox parsed too."""
    from pyshacl import validate as _shacl_validate
    data = Graph()
    for t in graph:
        data.add(t)
    data.parse(QUERY_TBOX, format="turtle")
    shapes = Graph()
    shapes.parse(QUERY_SHAPES, format="turtle")
    conforms, _rg, report_text = _shacl_validate(
        data, shacl_graph=shapes, inference="none", advanced=False
    )
    msgs = [
        line.split("Message:", 1)[1].strip()
        for line in report_text.splitlines()
        if "Message:" in line
    ]
    return Report(conforms=conforms, messages=msgs)


def reconcile(graph, *, rdfs=True):
    """Just the unknowns sub-pipeline (propagate-granular fixpoint + reconcile).
    Assumes lift/complete-chains already ran (or aren't needed). Kept as a named
    entry point for callers that only want the coarse/fine residuals."""
    if rdfs:
        rdfs_closure(graph)
    run_rule_fixpoint(graph, str(RULE_PROPAGATE_GRANULAR))
    run_rule(graph, str(RULE_RECONCILE))
    return graph

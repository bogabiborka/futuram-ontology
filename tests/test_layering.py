# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "owlrl", "pytest", "pyyaml", "openpyxl"]
# ///
"""Layering guard — package dependency rules: common depends on nothing; etl is
oracle-free; builder is RDF-in/RDF-out (no etl/oracle/poc/fs); poc extends builder;
oracle is tests-only. Static AST import-edge checks over src/ plus one runtime check."""
import ast
import pathlib
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


def _imports(pkg):
    """Set of (module_file_relpath, imported_root_pkg) for every top-level module
    imported by any .py under src/<pkg>. Reads the AST; lazy imports inside a
    function are still counted (the edge exists in the source)."""
    edges = set()
    for f in sorted((SRC / pkg).rglob("*.py")):
        tree = ast.parse(f.read_text(), filename=str(f))
        rel = f.relative_to(SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    edges.add((str(rel), a.name.split(".")[0]))
            elif isinstance(node, ast.ImportFrom):
                # absolute import only (level 0); relative (from . import) stays
                # in-package and is not a cross-package edge.
                if node.level == 0 and node.module:
                    edges.add((str(rel), node.module.split(".")[0]))
    return edges


def _forbidden(pkg, banned):
    return sorted((src, imp) for src, imp in _imports(pkg) if imp in banned)


def test_oracle_is_not_under_src():
    """The oracle is tests-only: it lives at tests/oracle, not src/. No library
    package ships it; the real ETL path is transform -> composition_rdf."""
    assert not (SRC / "oracle").exists(), \
        "oracle must live under tests/, not src/ (it is the tests-only reference)"


def test_no_src_package_imports_oracle():
    """HARD rule: NOTHING under src/ imports the oracle — not even lazily. A
    src->oracle edge means the shippable library depends on the tests-only
    reference."""
    bad = []
    for pkg in ("common", "etl", "builder", "poc"):
        bad += _forbidden(pkg, {"oracle"})
    assert not bad, f"no src package may import oracle (tests-only reference): {bad}"


def test_common_depends_on_nothing_internal():
    """common is the bottom layer: it imports none of etl/builder/oracle/poc."""
    bad = _forbidden("common", {"etl", "builder", "oracle", "poc"})
    assert not bad, f"common must not import project layers: {bad}"


def test_builder_sees_only_rdf_no_etl_oracle_poc():
    """builder is RDF-in/RDF-out: it imports neither etl, oracle, nor poc. (The
    drivetrain axis reaches builder ONLY as the in-graph sliceAxis marker the ETL
    authored — builder never reads CSV/YAML/source layout.)"""
    bad = _forbidden("builder", {"etl", "oracle", "poc"})
    assert not bad, f"builder must not import etl/oracle/poc: {bad}"


def test_builder_consumes_no_etl_doc_dict():
    """builder consumes ONLY rdflib graphs — never the ETL `doc` dict or its
    producers (transform / composition_rdf / chain_from_doc / csv_to_rdf …). The
    doc is an ETL-internal intermediate; the builder gets RDF."""
    forbidden_names = {"transform", "transform_doc", "split_em_doc",
                       "composition_rdf", "chain_from_doc", "to_chain"}
    forbidden_mods = {"csv_to_rdf", "composition_rdf", "chain_loader"}
    offenders = []
    for f in sorted((SRC / "builder").rglob("*.py")):
        tree = ast.parse(f.read_text(), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module \
                    and node.module.split(".")[-1] in forbidden_mods:
                offenders.append((str(f.relative_to(SRC)), node.module))
            if isinstance(node, ast.Attribute) and node.attr in forbidden_names:
                offenders.append((str(f.relative_to(SRC)), node.attr, node.lineno))
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                offenders.append((str(f.relative_to(SRC)), node.id, node.lineno))
    assert not offenders, f"builder must not touch the ETL doc dict: {offenders}"


def test_builder_never_walks_a_directory():
    """builder is handed composition GRAPHS, never a directory: no glob/rglob/
    iterdir/scandir/walk/listdir under builder/. The corpus scan lives in ETL
    (etl.corpus.load_corpus); a dir-walk here is the 'builder sees a directory' smell."""
    walkers = {"glob", "rglob", "iterdir", "scandir", "walk", "listdir"}
    offenders = []
    for f in sorted((SRC / "builder").rglob("*.py")):
        tree = ast.parse(f.read_text(), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in walkers:
                offenders.append((str(f.relative_to(SRC)), node.attr, node.lineno))
    assert not offenders, f"builder must not walk directories: {offenders}"


def test_no_src_package_imports_poc():
    """poc is the TOP layer (poc extends builder); nothing UNDER it imports it.
    Only tests may import poc."""
    bad = []
    for pkg in ("common", "etl", "builder"):
        bad += _forbidden(pkg, {"poc"})
    assert not bad, f"no src package may import poc (poc -> builder, not reverse): {bad}"


def test_poc_imports_builder_not_oracle():
    """poc extends BUILDER (its MC plugins subclass builder plugins); it must not
    reach into the oracle for production behaviour."""
    imps = {imp for _, imp in _imports("poc")}
    assert "builder" in imps, "poc must extend builder (import it)"
    bad = _forbidden("poc", {"oracle"})
    assert not bad, f"poc must not import oracle: {bad}"


# the ETL REAL-DATA emitters (CSV/Excel -> composition RDF). Importing these must
# NOT pull the oracle in — a defence-in-depth runtime check on top of the static
# test_no_src_package_imports_oracle.
_REALPATH_MODULES = ["etl.composition_rdf", "etl.csv_to_rdf", "etl.buckets",
                     "etl.corpus", "etl.doc_slices", "etl.serve_corpus"]


def test_etl_realpath_import_is_oracle_free():
    """Importing the ETL real-data modules does not import the oracle package.
    Run in a SUBPROCESS so a previously-imported oracle (from another test) does
    not mask the edge."""
    import subprocess
    code = (
        "import sys\n"
        "sys.path.insert(0, %r)\n" % str(SRC) +
        "import " + ", ".join(_REALPATH_MODULES) + "\n"
        "leaked = [m for m in sys.modules if m == 'oracle' or m.startswith('oracle.')]\n"
        "assert not leaked, 'ETL real path pulled in oracle: %s' % leaked\n"
        "print('ok')\n"
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"real-path import leaked oracle:\n{r.stdout}\n{r.stderr}"

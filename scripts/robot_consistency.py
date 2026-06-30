#!/usr/bin/env -S uv run --quiet --with rdflib python
# /// script
# requires-python = ">=3.11"
# dependencies = ["rdflib"]
# ///
"""Optional SECOND-line consistency check (SHACL stays the first-line gate):
STAR-extract EMMO/CEON/ChEBI terms, merge with the data + DQV/PROV-O, robot reason
with ELK.

ALWAYS checks the full TBox + ABox. A pre-reasoning EL-materialization step
(_materialize_el) precomputes the consequences of the ontology's non-EL axioms
(the hasDirectPart/isDirectPartOf inverse and the component-layer
equivalentClass-over-an-inverse definitions), so ELK gives a COMPLETE verdict over
the whole graph in minutes — no TBox-only fast path or DL reasoner needed. The
materialised triples are used ONLY for this check; the pipeline continues on the
original, non-materialised graph.

Usage:
    uv run scripts/robot_consistency.py                  # ELK, full TBox+ABox
    uv run scripts/robot_consistency.py --target output  # fq: view only
    uv run scripts/robot_consistency.py --input g.ttl    # check one file
    uv run scripts/robot_consistency.py --keep -o out/   # keep intermediates

Exit: 0 = consistent, 1 = unsatisfiable/inconsistent, 2 = setup error.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rdflib import RDF, OWL, URIRef, Graph

ROOT = Path(__file__).resolve().parent.parent

# Upstream ontologies live one-dir-per-ontology under ontology/sources/, each
# holding its source file + term-file (established layout: chebi/, metal-wheel/).
SOURCES = ROOT / "ontology" / "sources"

# Durable per-target merged artifacts (gitignored): each target merges to
# <target>-full.owl, reasons on that, writes <target>-full.ttl — the legacy
# merge->full.owl->reason shape, one artifact per target.
BUILD = ROOT / "consistency" / "build"

# STAR extraction sources: (base ontology, term-file, label). ChEBI's source is
# gzipped, so _ensure_chebi_source() gunzips on demand, or falls back to the
# pre-built chebi-module.ttl in the Fuseki /query data dir.
EXTRACTS = [
    # Use the SELF-CONTAINED vendored emmo.ttl (61k triples, 0 owl:imports, all 99
    # term-file IRIs present), NOT the 27-triple emmo-full.ttl stub whose only
    # content is 8 owl:imports that ROBOT's STAR extract would chase over the
    # network (w3id.org) — hanging/failing offline and in import-restricted CI.
    (SOURCES / "emmo" / "emmo.ttl", SOURCES / "emmo" / "emmo-term-file.txt", "emmo"),
    (SOURCES / "ceon" / "ceon-base.owl", SOURCES / "ceon" / "ceon-term-file.txt", "ceon"),
    (SOURCES / "chebi" / "chebi_core.owl", SOURCES / "chebi" / "chebi-term-file.txt", "chebi"),
]
CHEBI_GZ = SOURCES / "chebi" / "chebi_core.owl.gz"
CHEBI_MODULE = ROOT / "fuseki" / "futuram" / "data" / "query" / "chebi-module.ttl"

# Merged in directly (no extraction needed).
SUPPORT = [SOURCES / "_support" / "dqv.ttl", SOURCES / "_support" / "prov-o.ttl"]

# Two check targets, both run by `--target both` (default). Inputs are DISCOVERED
# via whole-directory globs; external bases added by _check_target() on top.
#   input  = the raw composition-statement graph (every TBox + composition TTL + metal-wheel).
#   output = the DERIVED fq: view (futuram.ttl + query TBox + ChEBI bridge + sidecars).
_TBOX = ROOT / "ontology" / "tbox"
_COMP = ROOT / "fuseki" / "futuram" / "data" / "composition"
_QUERY = ROOT / "fuseki" / "futuram" / "data" / "query"
_MW = ROOT / "ontology" / "sources" / "metal-wheel"

# TBoxes that describe the RAW statement model (everything except the query
# projection TBox, which belongs only to the output side).
_INPUT_TBOXES = sorted(p for p in _TBOX.glob("*.ttl")
                       if p.name != "composition-query.ttl")
# TBoxes the fq: view needs checked SEMANTICALLY (not just IRI-hygiene): the query
# projection TBox, the futuram->ChEBI bridge, and the disjointness axioms. All
# EL-safe (subClassOf + AllDisjointClasses over named classes).
_OUTPUT_TBOXES = [
    _TBOX / "composition-query.ttl",
    _TBOX / "futuram-chebi-bridge.ttl",
    _TBOX / "apollo-futuram-bridge.ttl",
    _TBOX / "ceon-futuram-bridge.ttl",
    _TBOX / "emmo-futuram-bridge.ttl",
    _TBOX / "prov-futuram-bridge.ttl",
    _TBOX / "composition-statement.ttl",
    _TBOX / "futuram-hierarchy.ttl",
    _TBOX / "element-disjointness.ttl",
]

INPUTS = {
    "input": [
        *sorted(_COMP.rglob("*.ttl")),
        *_INPUT_TBOXES,
        *sorted(_MW.glob("*.ttl")),          # TBox + ABox + criticality ABox
    ],
    "output": [
        *sorted(_QUERY.glob("*.ttl")),       # futuram.ttl + all served sidecars
        *_OUTPUT_TBOXES,
    ],
}

# Axioms a reasoner can actually find a contradiction in. Below this the run is
# an IRI-hygiene check, not a semantic one -- so we say so rather than imply
# "validated".
SEMANTIC_PREDICATES = (
    "http://www.w3.org/2002/07/owl#disjointWith",
    "http://www.w3.org/2002/07/owl#complementOf",
    "http://www.w3.org/2002/07/owl#disjointUnionOf",
    "http://www.w3.org/2002/07/owl#someValuesFrom",
    "http://www.w3.org/2002/07/owl#allValuesFrom",
    "http://www.w3.org/2002/07/owl#cardinality",
    "http://www.w3.org/2002/07/owl#maxCardinality",
    "http://www.w3.org/2002/07/owl#qualifiedCardinality",
)
SEMANTIC_TYPES = ("http://www.w3.org/2002/07/owl#AllDisjointClasses",)
SEMANTIC_MIN = 5  # fewer than this -> warn the run is hygiene-only


def _die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# Raise JDK's XML entity-size guard (ChEBI's OWL/XML trips it) and give the
# reasoner heap — the legacy compose.yml ROBOT_JAVA_ARGS knobs. Only applied
# when the caller hasn't set ROBOT_JAVA_ARGS itself.
_ROBOT_JAVA_ARGS = (
    "-Djdk.xml.maxGeneralEntitySizeLimit=0 "
    "-Djdk.xml.totalEntitySizeLimit=0 -Xmx24g"
)


def _robot(*args: str) -> None:
    """Run robot, streaming output; raise on failure."""
    cmd = ["robot", *args]
    print("  $ " + " ".join(cmd))
    env = dict(os.environ)
    env.setdefault("ROBOT_JAVA_ARGS", _ROBOT_JAVA_ARGS)
    res = subprocess.run(cmd, cwd=ROOT, env=env)
    if res.returncode != 0:
        _die(f"robot exited {res.returncode} for: {' '.join(args[:2])}", 2)


def _ensure_chebi_source() -> Path | None:
    """Gunzip the shipped ChEBI .gz on first use (idempotent), returning the
    decompressed .owl. None if neither .gz nor .owl is present (caller then
    falls back to the pre-built chebi-module.ttl)."""
    owl = SOURCES / "chebi" / "chebi_core.owl"
    if owl.exists():
        return owl
    if CHEBI_GZ.exists():
        import gzip
        print(f"  gunzip {CHEBI_GZ.relative_to(ROOT)} (one-time) ...")
        with gzip.open(CHEBI_GZ, "rb") as fin, open(owl, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        return owl
    return None


def _load_data(inputs: list[Path]) -> Graph:
    """Parse all data inputs into one rdflib Graph (fail loudly on a bad file)."""
    g = Graph()
    for p in inputs:
        if not p.exists():
            _die(f"input not found: {p}")
        before = len(g)
        g.parse(p)  # rdflib infers ttl/owl/rdf by extension/content
        print(f"  + {p.relative_to(ROOT)}  (+{len(g) - before} triples)")
    return g


# EL-materialization rules: precompute the consequences of the OWL axioms that are
# OUTSIDE the EL profile (the hasDirectPart/isDirectPartOf inverse and the
# component-layer equivalentClass-over-an-inverse definitions), so the reasoner
# checks an already-materialised, EL-complete graph. The DL axioms stay in the TBox
# as the spec; these CONSTRUCTs assert their results as data. Run BEFORE reasoning.
_EL_RULES_DIR = ROOT / "rules" / "el-materialization"
_EL_INVERSE_RULE = _EL_RULES_DIR / "infer-direct-part-inverse.rq"
_EL_LAYERS_RULE = _EL_RULES_DIR / "infer-component-layers.rq"


def _materialize_el(g: Graph) -> Graph:
    """Run the EL-materialization CONSTRUCT rules over `g` (mutates, returns). The
    inverse rule runs once; the layer rule runs to a fixpoint (each pass types the
    next layer down). Pure CONSTRUCT — no reasoner needed."""
    inv = _EL_INVERSE_RULE.read_text()
    before = len(g)
    for t in g.query(inv):
        g.add(t)
    layers = _EL_LAYERS_RULE.read_text()
    grew = True
    while grew:
        n = len(g)
        for t in g.query(layers):
            g.add(t)
        grew = len(g) > n
    print(f"  EL-materialization: +{len(g) - before} triples "
          f"(isDirectPartOf inverse + component-layer typings)")
    return g


def _count_semantic_axioms(g: Graph) -> int:
    n = 0
    for pred in SEMANTIC_PREDICATES:
        n += len(list(g.triples((None, URIRef(pred), None))))
    for t in SEMANTIC_TYPES:
        n += len(list(g.triples((None, RDF.type, URIRef(t)))))
    return n


def _check_target(name: str, inputs: list[Path], reasoner: str,
                  workdir: Path) -> bool:
    """Run extract -> merge -> reason for one target. Returns True if consistent
    (no unsatisfiable named classes), False otherwise."""
    print(f"\n=== target: {name}  (reasoner={reasoner}) ===")
    print("Loading data graph:")
    data = _load_data(inputs)
    # Materialise the EL-equivalent of the non-EL axioms BEFORE reasoning, so the
    # check runs on the intermediate (already EL-complete) result.
    _materialize_el(data)
    print(f"  data graph: {len(data)} triples")

    sem = _count_semantic_axioms(data)
    print(f"  reasoner-checkable axioms (disjoint/restriction/cardinality): {sem}")

    # Drop owl:imports: the modules are already co-loaded into `data`, so the
    # import triple is redundant and would make ROBOT try to resolve the IRI over
    # the network (fails offline/CI). Only this reasoner snapshot drops it.
    n_imports = len(list(data.triples((None, OWL.imports, None))))
    if n_imports:
        data.remove((None, OWL.imports, None))
        print(f"  (dropped {n_imports} owl:imports triple(s); modules co-loaded)")

    sub = workdir / name
    sub.mkdir(parents=True, exist_ok=True)
    data_nt = sub / "data.nt"
    data.serialize(destination=data_nt, format="nt")

    # 1) STAR-extract each external base against its term-file. ChEBI's source
    #    is gunzipped on demand; if it is unavailable, fall back to the
    #    pre-built chebi-module.ttl in the Fuseki /query data dir.
    extracted: list[Path] = []
    print("Extracting external terms (robot extract --method STAR):")
    for base, term, label in EXTRACTS:
        if label == "chebi":
            # The ChEBI STAR module is a pure function of source + term-file and is
            # already shipped as chebi-module.ttl. Reuse it when at least as new as
            # the term-file (re-extract from the 345 MB source only if missing/stale).
            if (CHEBI_MODULE.exists()
                    and CHEBI_MODULE.stat().st_mtime >= term.stat().st_mtime):
                print(f"  chebi: reusing pre-built {CHEBI_MODULE.relative_to(ROOT)} "
                      f"(up to date vs term-file; skips 345 MB re-extract)")
                extracted.append(CHEBI_MODULE)
                continue
            resolved = base if base.exists() else _ensure_chebi_source()
            if resolved is None:
                if CHEBI_MODULE.exists():
                    print(f"  chebi: source absent, term-file newer than module -- "
                          f"using possibly-stale {CHEBI_MODULE.relative_to(ROOT)}")
                    extracted.append(CHEBI_MODULE)
                else:
                    print("  chebi source AND module absent -- skipping ChEBI")
                continue
            print(f"  chebi: term-file newer than module -- re-extracting from "
                  f"{resolved.relative_to(ROOT)} (345 MB, slow)")
            base = resolved
        out = sub / f"{label}-extracted.owl"
        _robot("extract", "--method", "STAR",
               "--input", str(base), "--term-file", str(term),
               "--output", str(out))
        extracted.append(out)

    # 2) Merge the WHOLE target graph (extracts + support + data) into ONE
    #    durable artifact: consistency/build/<target>-full.owl. The reasoner runs
    #    on this single file (legacy's merge->full.owl shape, one per target).
    merge_inputs: list[Path] = [*extracted]
    merge_inputs += [p for p in SUPPORT if p.exists()]
    merge_inputs.append(data_nt)

    BUILD.mkdir(parents=True, exist_ok=True)
    full_owl = BUILD / f"{name}-full.owl"
    print(f"Merging -> {full_owl.relative_to(ROOT)} (robot merge):")
    margs: list[str] = []
    for p in merge_inputs:
        margs += ["--input", str(p)]
    _robot("merge", *margs, "--include-annotations", "true",
           "--output", str(full_owl))

    # 3) Reason on the merged full.owl: dump unsatisfiable classes and write the
    #    reasoned graph as <target>-full.ttl (Turtle, readable/diffable).
    unsat = sub / "unsatisfiable.owl"
    full_ttl = BUILD / f"{name}-full.ttl"
    print(f"Reasoning {full_owl.name} -> {full_ttl.relative_to(ROOT)} "
          f"(robot reason --reasoner {reasoner}):")
    _robot("reason", "--reasoner", reasoner,
           "--dump-unsatisfiable", str(unsat),
           "--annotate-inferred-axioms", "true",
           "--input", str(full_owl), "--output", str(full_ttl))

    # robot reason exits non-zero on inconsistency, which _robot turns into a
    # die(code 2). If we got here the ontology is CONSISTENT and every named
    # class is satisfiable (robot writes an empty unsat dump).
    unsat_classes: list[str] = []
    if unsat.exists():
        ug = Graph()
        ug.parse(unsat)
        unsat_classes = [
            str(s) for s in ug.subjects(RDF.type, OWL.Class)
            if not str(s).startswith("http://www.w3.org/2002/07/owl#")
        ]
    ok = not unsat_classes

    print()
    print(f"[{name}] artifacts: {full_owl.relative_to(ROOT)}, "
          f"{full_ttl.relative_to(ROOT)}")
    if ok:
        print(f"[{name}] consistent — no unsatisfiable classes.")
    else:
        print(f"[{name}] {len(unsat_classes)} UNSATISFIABLE class(es):")
        for c in unsat_classes:
            print(f"  - {c}")
        print(f"  (full dump: {unsat})")

    if sem < SEMANTIC_MIN:
        print(f"[{name}] CAVEAT: only {sem} reasoner-checkable axioms "
              f"(< {SEMANTIC_MIN}). This is effectively an IRI-hygiene / "
              "OWL-profile check, NOT a semantic validation of the composition "
              "statements (that is SHACL's job). Add material->ChEBI / "
              "ceonm:ChemicalElement mappings and element/level disjointness "
              "axioms to make this pass genuinely semantic.")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", "-t", choices=["input", "output", "both"],
                    default="both",
                    help="input = raw composition-statement graph; output = the "
                         "derived fq: view (checked after it is built); both "
                         "(default) = run input then output.")
    ap.add_argument("--input", "-i", action="append", type=Path,
                    help="override the data file(s) to check (repeatable). "
                         "Implies a single ad-hoc target; --target is ignored.")
    ap.add_argument("--outdir", "-o", type=Path, default=None,
                    help="where to write unsatisfiable.owl / core.owl per target "
                         "(default: a temp dir, removed unless --keep).")
    ap.add_argument("--keep", action="store_true",
                    help="keep intermediate extracts and the merged ontology.")
    args = ap.parse_args()

    if shutil.which("robot") is None:
        _die("`robot` not on PATH. Install ROBOT, or run via the Docker "
             "service: docker compose -f consistency/compose.yml run --rm check")
    for base, term, label in EXTRACTS:
        if not term.exists():
            _die(f"missing term-file for {label}: {term}")
        if label == "chebi":
            # extracted on demand from .gz, or via the pre-built module
            if not (base.exists() or CHEBI_GZ.exists() or CHEBI_MODULE.exists()):
                _die(f"no ChEBI source: need {base.name}, {CHEBI_GZ.name}, "
                     f"or {CHEBI_MODULE.name}")
        elif not base.exists():
            _die(f"missing base ontology for {label}: {base}")
    reasoner = "elk"

    if args.input:
        targets = {"adhoc": args.input}
    elif args.target == "both":
        targets = dict(INPUTS)
    else:
        targets = {args.target: INPUTS[args.target]}

    # The check ALWAYS runs over the full TBox + ABox. The EL-materialization step
    # (_materialize_el, run inside _check_target before reasoning) precomputes the
    # consequences of the non-EL axioms, so ELK gives a complete verdict over the
    # whole graph quickly — no need for the former TBox-only fast path.
    workdir = (args.outdir or Path(tempfile.mkdtemp(prefix="robot_consistency_")))
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"== ROBOT consistency check (targets={', '.join(targets)}) ==")
    try:
        results = {name: _check_target(name, inputs, reasoner, workdir)
                   for name, inputs in targets.items()}
    finally:
        if args.keep or args.outdir:
            print(f"\nintermediates kept in: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    print("\n== summary ==")
    for name, ok in results.items():
        print(f"  {name}: {'consistent' if ok else 'INCONSISTENT'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

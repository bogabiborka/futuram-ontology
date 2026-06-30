# Ontology consistency check

This checks the FutuRaM ontology for **logical contradictions** by running the
**ELK** OWL reasoner over the merged full graph (TBox + ABox). It is a
**second-line, optional** check. SHACL validation in the build pipeline is the
always-on gate, and this does not replace it.

The check **always runs over the full TBox + ABox**, using an **EL-materialization**
pre-step that lets ELK give a complete verdict in minutes (see
[How the check works](#how-the-check-works)).

If you just want to run it, jump to [Quick start](#quick-start).

---

## Quick start

### Option A: Docker (nothing to install but Docker)

From the repository root:

```bash
# Consistency check (ELK), both targets:
docker compose -f consistency/compose.yml run --rm check
```

That's it. The first run builds a small Docker image (a minute or two); later
runs are fast.

### Option B: locally (if you have `robot` and `uv`)

You need two tools on your `PATH`:

- ROBOT, the OWL toolkit. Install it from <https://robot.obolibrary.org/> (it is a
  Java jar, so you also need a Java 17+ runtime). Check with `robot --version`.
- uv, the Python runner. Install it from <https://docs.astral.sh/uv/>. Check with
  `uv --version`. (The script declares its own Python deps, so you do **not**
  need to set up a virtualenv.)

Then, from the repository root:

```bash
# Consistency check (ELK), both targets:
uv run scripts/robot_consistency.py
```

### What a result looks like

The run ends with a summary and an exit code:

```
== summary ==
  input: consistent
  output: consistent
```

| Exit code | Meaning |
|-----------|---------|
| `0` | Consistent; no contradiction found. |
| `1` | **Inconsistent**: a logical contradiction (or an unsatisfiable class) was found. |
| `2` | Setup error (e.g. `robot` not on PATH, a source file missing). |

If it reports `INCONSISTENT`, see [Troubleshooting](#troubleshooting).

---

## How the check works

The reasoner is **ELK**: fast, low-memory, and complete for the EL profile
(class hierarchy, existential restrictions, and **disjointness** via
`owl:disjointWith` / `owl:AllDisjointClasses`).

The check **always runs over the full TBox + ABox**. Before reasoning, an
**EL-materialization** step precomputes the consequences of the ontology's few
*non-EL* axioms: the `hasDirectPart` / `isDirectPartOf` inverse pair and the
component-layer `equivalentClass`-over-an-inverse definitions, via the SPARQL
CONSTRUCT rules in `rules/el-materialization/`. With those consequences asserted
as plain triples, **ELK gives a complete verdict** over the whole graph in minutes.
The materialised triples are used **only for the check**, and the pipeline continues on
the original, non-materialised graph.

Deep semantic validation of the statement *values* remains SHACL's job (the
always-on first-line gate); this reasoner check looks only for logical
contradictions.

---

## Useful options

```bash
# Only check the raw model, or only the derived fq: view:
uv run scripts/robot_consistency.py --target input
uv run scripts/robot_consistency.py --target output

# Check one ad-hoc file instead of the built-in targets:
uv run scripts/robot_consistency.py -i path/to/file.ttl

# Keep the merged + reasoned artifacts for inspection:
uv run scripts/robot_consistency.py --keep
```

(Pass any of these after the service name with Docker, e.g.
`docker compose -f consistency/compose.yml run --rm check --target input`.)

---

## What it actually does (for the curious)

For each **target** it loads the full graph, runs the EL-materialization rules,
then four ROBOT steps:

0. **EL-materialize.** Run the SPARQL CONSTRUCT rules in
   `rules/el-materialization/` over the loaded graph to assert the consequences of
   the non-EL axioms (the `isDirectPartOf` inverse plus the component-layer typings),
   so ELK reasons over an already-EL-complete graph. These triples exist only in
   this check's scratch graph.
1. **Extract** the handful of referenced EMMO and CEON terms from their upstream
   ontologies (`robot extract --method STAR`). EMMO is extracted from the
   self-contained `ontology/sources/emmo/emmo.ttl` (no `owl:imports`, so the
   extract needs **no network**). ChEBI is **not** re-extracted from
   its 345 MB source every run: the pre-built module
   `fuseki/futuram/data/query/chebi-module.ttl` (~2.4k triples) is reused as
   long as it is up to date with `ontology/sources/chebi/chebi-term-file.txt`. So
   the reasoner never touches the full ChEBI, and only the small module enters the
   merge. (If you add ChEBI terms, the script re-extracts from the source once,
   automatically.)
2. **Merge** those extracts plus DQV/PROV-O plus the target's data into one **union**
   file, `consistency/build/<target>-full.owl`.
3. **Reason** over that union with `--dump-unsatisfiable`, writing the reasoned
   result to `consistency/build/<target>-full.ttl` and reporting any
   contradiction. (Both build artifacts are gitignored.)

The input files are **discovered** by globbing `ontology/tbox/`, the metal-wheel
sources, and the composition/query data dirs, so a new TBox or data file is
included automatically.

`ROBOT_JAVA_ARGS` (lifted XML entity-size limits + `-Xmx24g` heap) is set for you.

### The two targets

- `input` is the raw composition model (the baseline): the statement TBox, the class
  hierarchy, and the uncertainty/distribution TBoxes, plus the live per-drivetrain
  composition data and the full metal-wheel ontology.
- `output` is the derived `fq:` serving view, the query-optimized case
  (`fuseki/futuram/data/query/futuram.ttl`), checked **after** it is built, with
  its query TBox and served sidecars.

Running both confirms the derivation introduced no new inconsistency.

---

## Troubleshooting

**`robot` not found.** Install ROBOT (<https://robot.obolibrary.org/>) and a Java
17+ runtime, or use the Docker option, which bundles both.

**It reports `INCONSISTENT`.** A real logical contradiction was found. To locate
it:

1. Re-run with `--keep` so the merged ontology is saved to
   `consistency/build/<target>-full.owl`.
2. Open that file in [Protégé](https://protege.stanford.edu/), then run a reasoner
   and choose "Explain inconsistency". ROBOT itself cannot explain an *inconsistent*
   ontology, only an *incoherent* (unsatisfiable-class) one.

**The check runs out of memory.** The full ABox is loaded into rdflib before
merging, so raise the heap with
`ROBOT_JAVA_ARGS="... -Xmx48g" uv run scripts/robot_consistency.py`, or check one
target at a time (`--target input` / `--target output`).

---

## Scope (important)

OWL reasoning only finds a contradiction where disjointness / restriction /
cardinality axioms exist. The schema (TBox) carries these, including the
component-layer disjointness and the EL-materialized layer typings, whereas the bulk
composition *statements* assert little disjointness. So a clean **ELK** run
guarantees the schema (with its materialized non-EL consequences) has no
disjointness / restriction contradiction against the full data. Deep semantic
validation of the statement *values* remains SHACL's job. The script prints a
`CAVEAT` line whenever a target has very few reasoner-checkable axioms, so a green
result is never silently over-trusted.

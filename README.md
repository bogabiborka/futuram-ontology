# A Semantic Knowledge Graph for Mapping Composition and Recoverability of End-of-Life Products

This repository ([bogabiborka/futuram-ontology](https://github.com/bogabiborka/futuram-ontology))
accompanies the paper *"A Semantic Knowledge Graph for Mapping Composition and
Recoverability of End-of-Life Products"* (Boga, Grau, Rösslein, Francisco
Morgado, Remmen).

It contains the ontology, the data, and the software described in the paper:
everything needed to read the model, query it, or reproduce the results.

## Abstract

The European green and digital transitions depend on strategic and critical raw
materials, whose supply faces significant disruption risks. Circular economy
strategies, such as recycling from the urban mine, are essential to mitigate
these risks. However, assessing the urban mine's potential is challenging due to
product heterogeneity, poor traceability, variable material stocks, and
fragmented composition data lacking interoperability across hierarchical levels
(product–component–material–element). To address these issues, we developed a
product-centric waste-stream composition knowledge graph and its underlying
ontology, enabling a structured and interoperable representation of composition
data across hierarchical levels. The composition ontology was extended with the
metal wheel of recycling to provide insights into recovery routes. Ontology
alignment was achieved using ROBOT, an open-source library for automating
ontology development, ensuring integration into existing semantic frameworks.
Two cases were analyzed: a baseline and a query-optimized ontology. Our
semantic-driven approach enables seamless navigation across composition levels,
supports reasoning, and facilitates comprehensive assessments of material
availability. We demonstrate semantic queries (SPARQL) and introduce a natural
language interface powered by a large language model. The resulting knowledge
base is extendable, compatible with LLM-based applications, and supports urban
mine evaluation and product design.

**Keywords:** recoverability, secondary raw materials, metal wheel of recycling,
interoperability, ontology

---

## Demo

A short demo putting the **same natural-language question** to both approaches,
the **baseline** dataset and the **query-optimized** dataset, and showing their
reasoning side by side. The clip below is the full run, fast-forwarded.

![Demo: the same NL question answered against the baseline and the query-optimized datasets](docs/media/ask-fq-vs-composition.gif)

> The question asked: *"What is the theoretical copper content of a standard 2025
> battery electric vehicle, and which recovery routes enable the co-recovery of
> copper with other base metals?"*

The full benchmark results are published at
**https://bogabiborka.github.io/futuram-ontology/**, and the whole run is
reproducible locally (see [Reproducing the results](#reproducing-the-results)).

---

## For readers of the paper

You do not need to install anything to explore the work:

- Browse the benchmark online. A live, browsable view of the natural-language
  benchmark is published at
  **https://bogabiborka.github.io/futuram-ontology/**, with the competency
  questions, the ground-truth answers, the model's responses, and its full
  reasoning transcripts. It runs the query-optimized ontology against the
  baseline head-to-head, so you can see where the precomputed view lets a
  language model answer questions the baseline graph forces it to derive by hand.
  No setup; open it in a browser.

- Download the ontology. The validated ontology, the derived query view, and
  their SHACL shapes are published as versioned, self-contained assets on the
  [Releases](https://github.com/bogabiborka/futuram-ontology/releases) page
  (latest: [`v0.2.0`](https://github.com/bogabiborka/futuram-ontology/releases/tag/v0.2.0)).
  Load the Turtle into Fuseki, `rdflib`, or any triple store; no builder is
  required. See [Versioning & releases](#versioning--releases).

- Read the model. [How it works, and how it is tested](#how-it-works-and-how-it-is-tested)
  links to the architecture, the two vocabularies, and the testing docs.

The two cases in the paper map to two SPARQL endpoints over the **same data**:

| case in the paper | endpoint | vocabulary |
|---|---|---|
| query-optimized | `/query/sparql` | the flat, precomputed `fq:` view, where one hop answers most questions |
| baseline | `/composition/sparql` | the rich, reified `futuram:` composition statements, multi-hop |

---

## Reproducing the results

> **This section is for reproducing the paper's numbers and figures.** If you
> only want to read or query the ontology, the [previous section](#for-readers-of-the-paper)
> is all you need.

### What you need first

| requirement | why | check |
|---|---|---|
| Docker (with Compose v2) | runs the whole stack: triplestore, search index, LLM tool server, web UI | `docker compose version` |
| One LLM to drive the benchmark | the benchmark is an LLM-to-SPARQL loop. No model ships in the image; you bring one (see [Bring your own model](#bring-your-own-model-byok--byom)) | a cloud API key, a GitHub Copilot subscription, or a local Ollama |
| ~8 GB free RAM, a few GB disk | Fuseki + Qdrant + image builds | |

Optional, only for specific steps:

| requirement | needed for |
|---|---|
| `uv` ([install](https://docs.astral.sh/uv/)) | running the Python scripts/tests directly on the host (it manages all Python deps) |
| ROBOT + Java 17+ | the optional OWL consistency check on the host. Docker bundles both (see [`consistency/README.md`](consistency/README.md)) |
| Node.js | building the static results viewer locally (see [`viewer/README.md`](viewer/README.md)) |

The data is **already in the repo**: a `git clone` brings it with you, and
nothing is downloaded at startup.

### Run the system in one command

```sh
git clone https://github.com/bogabiborka/futuram-ontology.git && cd futuram-ontology
docker compose up            # add -d to run in the background; first run builds images
```

This brings up the whole system and opens the web UI at
**http://localhost:3737**:

```
docker compose up
  bench-fuseki    :47040  triplestore; serves the SAME FutuRaM data two ways:
                          /query/sparql (query-optimized) and /composition/sparql (baseline)
  bench-vectordb  :47343  Qdrant; the RAG search index over both backends' VoID
  bench-mcp       :47898  the tool server the LLM calls (run SPARQL, search docs, skills)
  bench-observer  :3737   the web UI (open this); run experiments, pick a model/provider,
                          sign into GitHub Copilot, watch runs stream live
                          (it launches the benchmark itself, inside the container)
```

The root `docker-compose.yml` `include:`s the service stack from
[`bench/docker-compose.bench.yml`](bench/docker-compose.bench.yml) and adds the
observer UI on top.

### Bring your own model (BYOK / BYOM)

The benchmark needs one LLM to drive it; none ships in the image. Open
http://localhost:3737, go to *Providers & keys*, and pick whichever you have:

- GitHub Copilot: click **Sign in with GitHub** (OAuth device login, no key to
  copy). It needs a Copilot subscription. The token is cached server-side and
  never shown.
- Cloud API key (Anthropic, OpenAI, Google Gemini, Groq, OpenRouter): set the
  matching environment variable *before* `docker compose up` so the container
  inherits it, then choose that provider in the UI.
  ```sh
  ANTHROPIC_API_KEY=sk-...  docker compose up   # or OPENAI_API_KEY / GOOGLE_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY
  ```
  Keys are read from the environment and never typed in the browser or written
  to disk.
- Local model via [Ollama](https://ollama.com): install it, `ollama pull` a
  model, and keep `ollama serve` running. The container reaches your host's
  Ollama automatically (`host.docker.internal:11434`).

If nothing is set up, the UI shows a banner explaining these options, and the
benchmark refuses to run with a clear message rather than a stack trace.

### Run the benchmark

Once a model is connected, click **New** under *Experiments* in the UI, choose a
testcases file, backends, and your model, then click **Run**. It streams into the
run browser. The full procedure, the testcases, the scoring, and the
baseline-vs-query-optimized comparison are documented in
[`bench/README.md`](bench/README.md).

### Reproduce the scalability table

The scalability numbers (how the system grows along the drivetrain axis) come
from a dedicated harness:

```sh
uv run scripts/scaling_bench.py --yes        # writes the scaling CSV; --dry-run prints the plan first
```

> **This derive is destructive.** It rewrites the committed TTLs under
> `fuseki/futuram/data/` for each drivetrain subset, so restore afterwards with
> `git checkout fuseki/futuram/data/`. Add `--endpoint` and `--baseline-endpoint`
> (pointing at the running `:47040` endpoints) to also capture query timing;
> without them, size/derive/RAM are still measured.

The LLM query-time figures depend on the model, provider, and hardware. Each
experiment's environment is captured in its `run_meta` (visible in the observer
UI). The static viewer in [`viewer/`](viewer/README.md) bakes a published
experiment into a self-contained page.

### Query the data directly

While `docker compose up` is running, the FutuRaM data is served over SPARQL at
**http://localhost:47040**:

| endpoint | the paper's name |
|---|---|
| `:47040/query/sparql` | the query-optimized `fq:` view (one pattern answers most questions) |
| `:47040/composition/sparql` | the baseline composition statements (`futuram:`) |

The gold SPARQL solutions for every paper and competency question, written for
both backends, live under [`query-detail-solutions/`](query-detail-solutions/)
(`paper/` and `competency/`, each split into `fq/` and `composition/`). The query
pattern itself is in [`docs/architecture.md`](docs/architecture.md).

### Regenerate the served graph, validate, and test

To re-derive the served query-optimized graphs after code/data changes and
reload the running store:

```sh
uv run tests/build_instances.py futuram     # writes fuseki/futuram/data/...  (bench years 2010/2020/2025/2030/2050)
RELOAD=1 docker compose up -d               # re-parse the mounted TTL into TDB2
```

Validation runs in two tiers:

- SHACL is the always-on first-line gate (`src/common/pipeline.py` `validate()`:
  RDFS-close a copy, run the `shapes/` constraints; a non-conforming graph is
  never aggregated). It runs inside the build/test path automatically.
- OWL consistency is the optional second-line check; it reasons the graph against
  EMMO/CEON/ChEBI with ROBOT/ELK. It is off by default, run on demand.
  ```sh
  docker compose -f consistency/compose.yml run --rm check   # both targets, nothing to install but Docker
  uv run scripts/robot_consistency.py                        # or locally, if robot is on PATH
  ```
  It does not substitute for SHACL: SHACL checks data shapes, the reasoner checks
  logical contradictions. Full detail in
  [`consistency/README.md`](consistency/README.md).

Run the test suite:

```sh
uv run --with pytest --with pyyaml --with pyshacl --with rdflib \
  --with pandas --with openpyxl --with requests --with polars --with owlrl \
  python -m pytest tests/ -v --tb=line          # fast suite (slow CSV tests deselected)
uv run ... python -m pytest tests/ -v --tb=line -m slow   # the heavy real-CSV end-to-end tests
```

`--with polars` is required. Omitting it silently skips the SI
competency/uncertainty tests (they use `importorskip`), giving a misleading
green. More on the test architecture is in [`tests/README.md`](tests/README.md).

---

## How it works, and how it is tested

A short map; the detail lives in the sub-READMEs.

The measured composition is stored as **composition statements** (the **baseline**
model, `futuram:`), rich and reified on instances. The **builder** aggregates
those up to class-level estimates and projects them into the **query-optimized**
view (`fq:`), a flat vocabulary served over SPARQL. The pipeline is RDF in, RDF
out: the ETL turns CSV/Excel into baseline composition RDF, and the builder turns
that into the query-optimized graph.

```
CSV / Excel  --(etl)-->  baseline composition statements  --(builder)-->  query-optimized view  -->  Fuseki :47040  -->  MCP :47898  -->  LLM loop (the :3737 UI)
```

For the detail:

- [`docs/architecture.md`](docs/architecture.md) covers the data flow, the
  builder's plugin resolver, the generic axis model, and the two vocabularies.
  The [repository layout](#repository-layout) is at the bottom of this README.
- The two vocabularies are the paper's two cases: the composition statements are
  the baseline (`/composition/sparql`), and the `fq:` view is the query-optimized
  case (`/query/sparql`). A reader of the paper uses the two endpoints by their
  paper names and does not need the `fq:` internals; the reference is in
  [`docs/architecture.md`](docs/architecture.md).
- [`tests/README.md`](tests/README.md) covers how the pipeline and plugins are
  tested: the oracle principle, the enforced layering, the L1-L5 pipeline layers,
  the plugin/serving coverage, and the SHACL gate.
- [`bench/README.md`](bench/README.md) covers the benchmark harness, and
  [`src/etl/README.md`](src/etl/README.md) the ETL.

---

## Versioning & releases

The **GitHub Release is the product**. CI derives the served view, validates it,
and publishes the verified Turtle as immutable release assets; there is no live
triplestore to push into. A Fuseki instance (or `rdflib`, or any store) loads a
release as an optional consumer; it is never a deploy target.

`config.json` tracks two independent schema versions and one free-id dataset.
The schema and the data change for different reasons and are versioned
separately:

| coordinate | what it versions | bump rule |
|---|---|---|
| `compositionSchema.version` | the core model: `composition-statement.ttl`, `futuram-hierarchy.ttl`, the uncertainty ruleset, the ChEBI bridge, and their SHACL shapes | SemVer: PATCH for clarifying, MINOR for additive (backward-compatible), MAJOR for breaking |
| `querySchema.version` | the `fq:` projection: `composition-query.ttl` and the resolver/plugin layer (`src/builder/resolver/`) | SemVer, independent of the composition schema |
| `dataset.id` | the ground-truth composition set (e.g. `elv-full`, `elv-bev-2020`) | a free label, not a SemVer; pins the composition schema it was built against |

The released file names make the lineage visible. The `fq:` ABox is derived from
a specific composition set, so the two are a bound pair, linked in the data by
`fq:derivedFromStatement` (a `prov:wasDerivedFrom` sub-property):

```
composition-statement-tbox-0.1.0.ttl                    # composition schema
futuram-query-tbox-0.1.0.ttl                            # fq: query schema (independent)
composition-abox-elv-full.ttl                           # ground set (free id)
futuram-query-0.1.0-based-on-comp-0.1.0+elv-full.ttl    # served view: query <q> on (comp <c> + dataset)
manifest.json                                           # versions, sha256s, derivedFrom binding
```

Read as: *"futuram-query 0.1.0, based on composition 0.1.0 + dataset elv-full"*.

`.github/workflows/deploy-data.yml` is the governance pipeline: a PR into `main`
runs the validation gate; a `v*` tag builds and publishes the release. The
validation brackets the derivation with a consistency check on each side:

1. reason over the composition statements + their TBox (input consistency);
2. derive the `fq:` view by running the resolver/plugin (`build_instances`);
3. reason over the *derived* `fq:` view (the derivation introduced no
   inconsistency);
4. `scripts/ci_release_assets.py --validate-only` conforms the served graph and
   checks every `fq:derivedFromStatement` resolves into the shipped composition
   set (no dangling pointers);
5. full `pytest` suite (competency-question regression).

The reasoning layer uses **ELK** (EL profile, low-memory) so it runs on a stock
GitHub runner; an EL-materialization pre-step keeps it complete for the
ontology's few non-EL axioms without a full-DL reasoner. The published release is
loadable and queryable standalone, with no builder and no Fuseki required.

### Branching

`main` is always stable, the trunk from which releases are cut; never push to it
directly. Branch off `main`, open a PR (CI runs the validation gate: SHACL +
consistency + full `pytest`), merge when green, then cut a release by tagging
`main` with a `v*` tag. A PR into `main` validates but publishes nothing; a `v*`
tag builds and publishes.

---

## Repository layout

| dir | what |
|---|---|
| `src/builder/` | builds the served `fq:` graph from composition RDF. `derive.py` (`merge_sources` + `derive_all`), `store.py` (incremental `add_source`), `slicer.py` (generic year/value axis slicing), `resolver/` (per-constituent projection plugins), `aggregate.py`, `index.py`. Never reads CSV/YAML/dirs or the ETL doc dict. |
| `src/etl/` | source -> composition RDF. `csv_to_rdf.py` (CSV/Excel -> `doc` dict), `composition_rdf.py` (`doc` -> composition RDF, oracle-free), `chain_loader.py` (material canonicalization), `buckets.py`/`corpus.py` (year-bucketing + the `load_corpus` dir scan), `build_fq.py` (on-disk build entry); `input/`, `output/` |
| `src/poc/` | proof-of-concept extensions to the builder; kept separate from the core derive |
| `src/common/` | shared primitives: `vocab.py` (namespaces + content-addressed `stmt_iri`), `pipeline.py` (the SHACL `validate()` gate, RDFS closure, CONSTRUCT-rule runners, reserved `SKIP_NAMES`), `fuseki_client.py` |
| `ontology/tbox/` | the TBoxes: `composition-statement.ttl` (the baseline `futuram:` model), `composition-query.ttl` (the query-optimized `fq:` view), `futuram-hierarchy.ttl` (taxonomy), `futuram-chebi-bridge.ttl`, `element-disjointness.ttl`, `apollo-sv-distributions.ttl`, `uncertainty-ruleset.ttl` |
| `ontology/sources/` | upstream ontologies, one dir each: `emmo/`, `ceon/`, `chebi/` (gzipped), `metal-wheel/`, `_support/` (dqv, prov-o) |
| `ontology/abox/` | example/sample instance data |
| `shapes/` | SHACL constraints (the first-line `validate()` gate) |
| `rules/` | SPARQL CONSTRUCT rules (complete-chains, infer-class-composition, reconcile, check-mass-conservation, the EL-materialization pre-step, ...) |
| `fuseki/` | the one committed Fuseki datasource (`futuram`): `data/composition/` (the baseline) + `data/query/futuram.ttl` (the served query-optimized view) |
| `query-detail-solutions/` | gold SPARQL solutions for every paper + competency question, for **both** backends: `paper/` and `competency/`, each split into `fq/` and `composition/` |
| `bench/` | the LLM benchmark: the harness, testcases, VoID generation, the observer web UI. See [`bench/README.md`](bench/README.md) |
| `sparql-llm/` | the chat + MCP server over the endpoints, taught the `fq:` vocabulary. See [`sparql-llm/README.md`](sparql-llm/README.md) |
| `consistency/` | optional second-line ROBOT/ELK consistency check. See [`consistency/README.md`](consistency/README.md) |
| `viewer/` | static GitHub Pages viewer for published experiment results (Vite + React). See [`viewer/README.md`](viewer/README.md) |
| `tests/` | the test suite + `build_instances.py` (the per-backend `fq:` build entry point); `oracle/` (the tests-only aggregation reference); `specs/` (per-test specifications). See [`tests/README.md`](tests/README.md) |
| `scripts/` | operational scripts: `download_zenodo.py` (fetch ELV CSVs), `robot_consistency.py`, `scaling_bench.py` + `scaling_to_word.py` (the scalability table), `gen_void.py`, `ci_release_assets.py`, freeze/migrate helpers |
| `data/` | the ELV CSVs (fetched from Zenodo) + `data/onecar/` Excel |
| `docs/` | this architecture doc, plus README media (`docs/media/`). See [`docs/architecture.md`](docs/architecture.md) |

How the architecture is enforced by the test suite is covered in
[`tests/README.md`](tests/README.md). RDF work must use `uv` + `rdflib`, per
`CLAUDE.md`.

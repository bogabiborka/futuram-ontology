# Benchmark: baseline vs query-optimized, head to head

Ask the **same natural-language question** of an LLM-to-SPARQL loop against two
backends of the **same data**, and compare time, token cost, and correctness. The
two backends are the paper's two cases:

| backend | endpoint | the paper's name | shape |
|---|---|---|---|
| `fq` | `/query/sparql` | query-optimized | flat, precomputed `fq:contains`/`fq:amount` on classes; one hop |
| `composition` | `/composition/sparql` | baseline | raw, reified `futuram:CompositionStatement` on instances; multi-hop, mixed units, no aggregate |

The hypothesis is that the query-optimized view lets the LLM answer aggregated
composition questions correctly in one hop, while the baseline forces it to walk
the `hasCompositionStatement` / `hasPartRelation` / `refersTo` tree, normalise
kg/kg vs g/kg, and aggregate by hand. The baseline path is slower and more
error-prone.

## Run it with `docker compose up`

The benchmark is part of the one-command stack. From the repo root:

```sh
docker compose up        # add -d for background; first run builds images
```

This brings up everything and opens the web UI at http://localhost:3737:

```
docker compose up   (root docker-compose.yml includes bench/docker-compose.bench.yml)
  bench-fuseki    :47040  /query/sparql (query-optimized) + /composition/sparql (baseline)
  bench-vectordb  :47343  Qdrant; the RAG index over both backends' VoID
  bench-mcp       :47898  the tool server the LLM calls (run SPARQL, search docs, skills)
  bench-observer  :3737   the web UI; runs the benchmark INSIDE the container
```

On first `up`, a one-shot materialises the served TTLs, generates both VoID
files, and indexes them into the `futuram_bench` Qdrant collection, then the MCP
server comes up. You drive the benchmark entirely from the web UI; there is no
separate stack to launch and nothing to run by hand.

### Bring your own model

No model ships in the image; you connect one in the UI under *Providers & keys*
(GitHub Copilot via sign-in, a cloud API key via an env var, or a local Ollama).
The three options and their key handling are in the root README's
[Bring your own model](../README.md#bring-your-own-model-byok--byom). The
provider/model selection from the command line is covered in
[Selecting the model / provider](#selecting-the-model--provider) below.

### Run an experiment

In the UI, click **New** under *Experiments*, choose a testcases file, backends
(`fq`, `composition`, or both), and your model, then click **Run**. The run
streams live into the run browser: every attempt, every SPARQL query, and every
tool result, scored against the ground truth.

## Testcases

The questions and ground-truth answers live in `bench/testcases/` (you own
these). A question never names an ontology class; the LLM must resolve plain
language to a class IRI.

| file | what |
|---|---|
| `domain.yaml` | the combined domain benchmark, the union of the two splits below |
| `domain-competency.yaml` | the SI's ten natural-language competency questions |
| `domain-sparql.yaml` | the four special SPARQL-query questions (SI-5/6/7 + embedded controllers) |
| `domain-recovery.yaml` | the recovery/recoverability questions (metal-wheel routes) |
| `error-domain.yaml` | adversarial / error-handling cases |

The gold SPARQL solutions for these questions, written for both backends, are in
[`query-detail-solutions/`](../query-detail-solutions/) (`paper/` and
`competency/`, each split into `fq/` and `composition/`).

## What the loop enforces

- Per-task timing. Every tool call (`search_sparql_docs`, `get_classes_schema`,
  `execute_sparql_query`) is individually timed. Each run carries a
  `tool_seconds_by_type` breakdown and the ordered `tool_timings`; the summary
  totals tool time per backend.
- Re-prompt until a result. If an attempt ends with no parseable `ANSWER:` line,
  the harness re-prompts (a fresh attempt that tells the model what went wrong)
  until it answers or the timeout fires. `attempts` is reported per run.
- Tool-grounded answers only. An answer is accepted only if the model actually
  ran `execute_sparql_query` and it returned results in the attempt that produced
  it. A "from memory" answer is rejected and re-prompted, and scored incorrect if
  it is all the model ever produces.
- Timeout per `(question, backend)` (`--timeout`, default 600 s). A run that hits
  it is marked as a timeout.
- Token accounting. Each call's input/output tokens are summed across the whole
  run (all attempts and tool round-trips), reported per test and per backend.
  This is where the cost gap shows: the baseline backend's multi-hop re-prompting
  burns far more tokens than the query-optimized backend's single hop.

## The harness loop

The harness connects to the `bench-mcp` MCP server, points it at one backend's
endpoint, and loops: the model learns the query method from the skills, searches
the docs, writes SPARQL, and the harness runs it through the VoID guards before
it reaches Fuseki. An answer counts only if it was grounded in a real query
result; otherwise the model is re-prompted with what went wrong. The full tool
and guard diagram, and the tool and guard reference tables, are in
[`sparql-llm/README.md`](../sparql-llm/README.md#the-harness-and-the-tools-it-exposes).

Each backend runs independently with the same question and the same tools; the
only difference is which endpoint the model queries. That isolation is what makes
the comparison fair. Set `BENCH_FILTER_DOCS_BY_ENDPOINT=0` to disable
per-endpoint doc filtering.

## VoID generation

Each backend is indexed from its own VoID, a machine-generated description of its
shape, never hand-authored:

| file | role |
|---|---|
| `gen_void_composition.py` | the VoID for the baseline (composition statements): the reified shape (typed blank nodes, the `CompositionStatement` to `QuantityInterval` to `QuantityValue` chain) and the unit inventory (kg/kg vs g/kg) |
| `scripts/gen_void.py` | the counterpart for the query-optimized `fq:` view (collapses the per-(drivetrain, component, year) slice classes into one chapter) |
| `futuram_examples_composition.ttl` | teaching SPARQL for the baseline endpoint: single-hop reads, unit normalisation, and the manual aggregation (pure `futuram:`, no `fq:`) |
| `settings.bench.json` | registers the two backends, each with its own VoID + examples + endpoint, for the MCP indexer |

In compose this runs automatically (the `void-gen` one-shot, before the
indexer). To regenerate the composition VoID by hand for inspection:

```sh
uv run bench/gen_void_composition.py fuseki/futuram/data/composition \
    -t ontology/tbox/composition-statement.ttl \
    -o bench/futuram_void_composition.ttl
```

## Running the harness directly (without the UI)

The observer runs `run_bench.py` for you inside the container; you can also call
it on the host (it needs the stack up for the endpoints and MCP server). Its
PEP-723 header lets `uv` handle dependencies: the provider SDKs (`ollama`, `mcp`,
`anthropic`, `openai`, `google-genai`) are imported lazily, so only the one you
use is needed.

```sh
# a scored testcases run, skills on, both backends:
uv run bench/run_bench.py bench/testcases/domain.yaml \
    --backends fq,composition --skills \
    --max-steps 16 --token-budget 1000000 --verbose \
    --json /tmp/results.json --transcripts bench/transcripts.zip

# a single ad-hoc question (unscored):
uv run bench/run_bench.py --ask "How much copper is in a battery electric vehicle?" \
    --skills --max-steps 16 --token-budget 400000
```

Key flags: `--backends` (which to run), `--skills` (expose the how-to skills via
`list_skills`/`get_skill`), `--provider`/`--model` (see below), `--max-steps`
(tool-loop steps per attempt, default 16), `--token-budget` (hard token cap per
`(question, backend)`), `--timeout` (wall-clock budget, default 600 s), `--json`
(light per-run metrics), and `--transcripts` (the full per-run zip, default
`bench/transcripts.zip`).

### Selecting the model / provider

Providers are named profiles in `bench/providers.json`. Each profile maps a name
to a `provider`, a default `model`, an optional `base_url`, and the name of the
env var that holds the key (`key_env`). API keys are never stored in the repo:
only the env-var name is, and the key is read from the environment at launch and
never written to transcripts, the results JSON, or the environment record.

```sh
uv run bench/run_bench.py --list-models                 # Ollama tags AND provider profiles

ANTHROPIC_API_KEY=sk-...  uv run bench/run_bench.py bench/testcases/domain.yaml --provider anthropic
OPENAI_API_KEY=sk-...     uv run bench/run_bench.py bench/testcases/domain.yaml --provider openai --model gpt-4o
GOOGLE_API_KEY=...        uv run bench/run_bench.py bench/testcases/domain.yaml --provider gemini

# or name the provider inline in the model tag (no profile needed):
ANTHROPIC_API_KEY=sk-...  uv run bench/run_bench.py bench/testcases/domain.yaml --model anthropic/claude-opus-4-8
```

`--model` overrides a profile's default model; `--provider` (or `$BENCH_PROVIDER`)
selects the profile. A bare `--model <tag>` with no provider stays on Ollama
(your host daemon, `--ollama-host`, default `http://localhost:11434`). For GitHub
Copilot, either set `COPILOT_API_KEY`/`GITHUB_TOKEN`, or run a one-time
device-flow login that caches the token under
`~/.config/futuram-bench/copilot.json`:

```sh
uv run bench/run_bench.py --login copilot     # opens github.com/login/device, enter the code
uv run bench/run_bench.py bench/testcases/domain.yaml --provider copilot
```

## Output

Per case, the run prints a pass/fail mark and the time/tokens for each backend,
then a per-backend summary (correctness, avg/median time, avg SPARQL count, avg
attempts, timeouts, total tokens). On the same question and data, the
query-optimized backend answers in one hop quickly and cheaply, while the
baseline backend issues many queries across re-prompted attempts, burns far more
tokens, and often still fails to assemble the multi-hop, unit-normalised
aggregation that the query-optimized view serves precomputed. Results vary by
model and hardware.

`--json` writes the light per-run metrics. The full conversation for every test
is stored compressed in a zip (`--transcripts`, default `bench/transcripts.zip`),
one `<case>__<backend>.json` per run, written incrementally. Each file holds
every system/user/assistant/tool turn, the SPARQL the model wrote, and the
complete tool results across all re-prompt attempts:

```sh
unzip -l bench/transcripts.zip                                  # list runs
unzip -p bench/transcripts.zip <case>__composition.json | jq .  # inspect one
```

## The observer web UI (`bench/observer/`)

A Next.js app (served on `:3737` by the `bench-observer` container) drives and
watches the benchmark:

- Experiments: save a named experiment (testcases file + backends + model), run
  it, and watch every `(case, backend)` run stream live with its status,
  attempts, tokens, and query count.
- Conversation: the selected run's turns, each a collapsible cell rendered as
  Markdown (fenced SPARQL highlighted), flagged `DATA` / `EMPTY` / `[next]` /
  `[helper]` / `[diagnose]`, with each corrective round shown as a rejection, its
  steps, then the answer.
- Ask: type a question in natural language, pick a provider profile (with an
  optional model override), and run it ad-hoc on both backends. The API key is
  never entered in the browser; a non-Ollama provider uses the key the server
  holds in the env var its profile names.
- Enter my results: set the expected (ground-truth) answer for a case (or paste
  the model's `ANSWER:` JSON) and save it into the testcases YAML, creating the
  case if new.

The container reads and writes a shared live-transcript dir (`bench/live/`) and
the testcases YAML (`bench/testcases/domain.yaml` by default). To use a cloud
provider from the UI, that provider's `key_env` (e.g. `ANTHROPIC_API_KEY`) must
be set in the environment that `docker compose up` inherits, so the spawned
`run_bench` sees it.

> A static, published view of one experiment's full results (for sharing without
> the stack) is built by the separate viewer. See
> [`viewer/README.md`](../viewer/README.md).

## Pointing at other data

The compose stack targets the one **futuram** datasource (its `/query` and
`/composition` endpoints), the same data the chat serves. To benchmark a
different dataset, point the same machinery at its two endpoints: regenerate its
two VoID files, swap the `endpoint_url`s in `settings.bench.json` (or a copy),
and re-index. The loop, the scoring, and the timing are identical, so the
baseline-vs-query-optimized comparison carries over unchanged.

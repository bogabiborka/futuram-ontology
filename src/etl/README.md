# ETL: source data to baseline composition statements

The ETL turns raw source data (CSV, Excel, or scenario YAML) into **composition
statements**: the **baseline** RDF model (`futuram:`), rich and reified on
instances. That baseline graph is what the builder then projects into the
**query-optimized** view (`fq:`). The ETL produces the baseline; it does not
produce the query-optimized view (that is the builder, see
[`docs/architecture.md`](../../docs/architecture.md)).

```
CSV / Excel / YAML  --(this ETL)-->  baseline composition statements (futuram:)  --(builder)-->  query-optimized view (fq:)
```

The ETL emits base-typed instances plus their generic signals (reference year,
drivetrain marker, period) and nothing else: it authors no time-slice and no
remainder. Both of those are derived later by the builder/resolver from the RDF.
The ETL never decides an aggregation axis; it only records the markers the
builder reads off the graph.

## Pieces

| file | role |
|---|---|
| `csv_to_rdf.py` | transform a FutuRaM CSV/Excel dataset into the intermediate `doc` dict (level-aware: product / component / material / element) |
| `composition_rdf.py` | emit baseline composition-statement RDF directly from a transform, oracle-free |
| `chain_loader.py` | doc-level material canonicalization for the real-data path |
| `canonicalize_materials.py` | canonicalize scenario-YAML materials to honest classes (one composition per material) |
| `limit_corrections.py` | load and apply the known DQV-adjustment corrections for the composition CSVs |
| `buckets.py` | chunk a dataset's composition RDF into N-year buckets + a catalog |
| `doc_slices.py` | doc-level (YAML side) time-slice authoring for the synthetic scenarios |
| `corpus.py` | digest a directory of composition RDF (any origin) into a catalog |
| `serve_corpus.py` | on-disk pipeline runner: scan a baseline composition RDF directory and feed it through |
| `project_corpus.py` | project a directory of baseline composition RDF into the query-optimized `fq:` dataset (this is the builder's on-disk entry, called from the ETL side) |

## Inputs and outputs

| dir | what |
|---|---|
| `input/futuram/` | the real ELV CSVs per drivetrain (`ELV_1980_2050_<drivetrain>.csv`), their `*_known_limit_corrections.csv`, and `oneCarOnly.xlsx` |
| `input/example/` | the small sample workbook (tracked) |
| `input/test/` | the synthetic scenario YAMLs (numbered `01-...` through the rest) the pipeline tests run on |
| `output/` | scratch output of an on-disk run |

## Running it

The per-backend build is driven from `tests/build_instances.py` (the entry point
that wires the ETL to the builder and writes the served datasource):

```sh
uv run tests/build_instances.py futuram     # -> fuseki/futuram/data/...  (bench years 2010/2020/2025/2030/2050)
```

The real ELV CSVs are fetched on demand (they are gitignored):

```sh
uv run scripts/download_zenodo.py
```

Layering rules the ETL must respect (enforced by `tests/test_layering.py`): the
ETL's real path is oracle-free, and the builder it feeds reads only RDF, never
the ETL `doc` dict or a directory. See
[`docs/architecture.md`](../../docs/architecture.md) and
[`tests/README.md`](../../tests/README.md).

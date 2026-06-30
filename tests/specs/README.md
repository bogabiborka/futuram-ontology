# Per-test specifications

One markdown per pipeline test so they can be reviewed independently. Each spec is
self-contained. It states the scenario, what is asserted, the oracle it is asserted
against, the expected outcome, and any open question for THAT test.

These specs document the layered pipeline tests in
[`tests/test_rdf_pipeline.py`](../test_rdf_pipeline.py). That file now covers
layers L1 through L5: well-formedness, conservation, class lift, coarse
fillers, and unknown residual. The specs below are the written-out form of the
L1 and L2 layers. The higher layers (L3 through L5) are exercised by the test functions
`test_L3_*`, `test_L4_*`, and `test_L5_*` directly.

## The oracle principle (applies to all)

No hand-typed expected numbers. The oracle (`tests/oracle/`) is a Python model of
a supply chain, where `fastchain/` is the implementation and `supplychain.py` a thin
facade over it. From a scenario description it computes the ground truth
(conservation sums, coarse-vs-granular totals, the quantified unknown); each test
asserts the pipeline reproduces what the model already knows. The scenarios are
loaded by `tests/scenarios.py`; the pipeline tests live in
`tests/test_rdf_pipeline.py`.

## Index

| spec | test function | layer | status |
|------|---------------|-------|--------|
| [L1.1](L1.1-wellformed-conform.md) | `test_L1_all_scenarios_wellformed_conform` | SHACL | GREEN |
| [L1.2](L1.2-bare-kg-rejected.md) | `test_L1_bare_kg_rejected` | SHACL | GREEN |
| [L1.3](L1.3-missing-metadata-rejected.md) | `test_L1_missing_metadata_rejected` | SHACL | GREEN |
| [L2.1](L2.1-conservation-matches-oracle.md) | `test_L2_conservation_matches_oracle` | rule + SHACL | GREEN |
| [L2.2](L2.2-overshoot-rejected.md) | `test_L2_overshoot_rejected_by_shacl` | rule + SHACL | GREEN |

GREEN = passes now (a characterization of built behaviour).

The L3–L5 layers (`test_L3_lift_produces_class_composition`,
`test_L4_coarse_statement_gets_unknown_fillers`,
`test_L4b_class_view_has_no_level_skips`,
`test_L5_unknown_residual_matches_oracle`,
`test_L5_cross_level_conservation_matches_oracle`, and so on) are implemented and green.
They don't yet have written-out spec markdown here.

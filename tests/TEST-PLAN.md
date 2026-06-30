# Test Plan — Composition Statement Pipeline (TDD)

Tests the pipeline defined in `AGGREGATOR-SPEC.md` against the model in
`composition-statement.ttl` / `composition-statement-shapes.ttl` and the rules.

## Approach

- **TDD**: for unbuilt features (chain completeness, coarse/fine reconciliation,
  scope, aggregation) the tests are written FIRST and fail (red) until the rule
  / aggregator is implemented. For already-built features the tests are
  characterization tests that lock in current behaviour.
- **Tiny hand-crafted fixtures**, one scenario per file under `fixtures/`, NOT
  the big generated `sample-instances.ttl`. Each fixture isolates exactly one
  behaviour so a failure points at one cause.
- **uv + rdflib + pyshacl**, pure SPARQL rules, core SHACL only (per CLAUDE.md
  and the spec). No advanced SHACL, no Python in the pipeline itself.

## Test layers, in dependency order

Each layer depends on the ones above it; test bottom-up.

### L1 — SHACL well-formedness  (BUILT — characterization)
The per-statement invariants already enforced by `composition-statement-shapes.ttl`.
- L1.1 a well-formed kg/kg statement conforms
- L1.2 a well-formed g/kg statement conforms
- L1.3 a bare-kg (absolute mass) statement is REJECTED ("per 1 kg of whole")
- L1.4 an item/count unit is REJECTED
- L1.5 missing required metadata (validity / provenance / production) each
  REJECTED
- L1.6 an Element used as a composition-statement whole (subject of
  `hasCompositionStatement`) is REJECTED (leaf rule)
- L1.7 whole/part that is a class (not an individual) is REJECTED

### L2 — Mass conservation, single whole  (BUILT — characterization)
`check-mass-conservation.rq` + the `massConserved` SHACL check.
- L2.1 fractions summing to < 1 (shortfall) → conforms (shortfall allowed)
- L2.2 fractions summing to exactly 1 → conforms
- L2.3 fractions summing to > 1 (overshoot) → massConserved=false → REJECTED
- L2.4 mixed kg/kg + g/kg normalised correctly in the sum (overshoot detected
  only after g/kg → kg/kg scaling)

### L3 — Class-composition lift  (BUILT — characterization)
`infer-class-composition.rq`.
- L3.1 an instance statement whole(classP)→part(classC) yields
  `P futuram:hasComposition C`
- L3.2 only futuram domain classes are related (no owl:Class / upper types)

### L4 — Chain completeness  (SPEC ONLY — red first)  [spec §3]
The resolved chain must match `(Product)+ (Component)+ Material Element`.
- L4.1 a full P→C→M→E chain is complete → conforms
- L4.2 nested P→subP→C→subC→M→E (repeated product/component) → conforms
- L4.3 a Material in the middle (…→M→C→…) → REJECTED (M only at bottom)
- L4.4 two Elements / two Materials in one chain → REJECTED
- L4.5 a coarse P→E statement with NO C/M → after `complete-chains` rule, gets
  `unknownComponent` + `unknownMaterial` inserted → then conforms
- L4.6 a coarse C→E statement → gets `unknownMaterial` inserted (component
  already known) → conforms

### L5 — Coarse/fine reconciliation  (SPEC ONLY — red first)  [spec §4–§6]
Direct = ceiling; unknown = direct − Σgranular; unknowns are disjoint residuals.
- L5.1 coarse == Σgranular → unknown(W,P) = 0, no unknown residual node
- L5.2 coarse > Σgranular → unknown(W,P) = gap, a labelled residual node carries it
- L5.3 Σgranular > coarse → cross-level overshoot → REJECTED
- L5.4 reconciliation holds at COMPONENT level too (motor→Cu ceiling), not just
  product/element
- L5.5 disjointness: coarse total == Σ(all coexisting chains incl. residual)
  exactly (partition, no double-count) — spec §6
- L5.6 incremental refinement: adding motor→Cu moves quantity from the
  all-unknown chain into the motor chain; residual shrinks accordingly — spec §5
- L5.7 forced placement: when only one component can hold the unknown, it is
  pinned there (calculated, not bucketed) — spec §8a tier 1
- L5.8 free residual: when nothing constrains location, it stays in a generic
  unknownComponent/unknownMaterial bucket; NOT imputed into real components —
  spec §8a tier 2 (no imputation)

### L6 — Ontological scope (defined classes)  (SPEC ONLY — red first)  [spec §7]
- L6.1 "motor" scope selects all elvElectricMotor instances (+ subClassOf*)
- L6.2 "small-car motor" defined class (≡ motor ⊓ partOf some SmallCar) is
  reasoner-populated with only motors part-of a small car
- L6.3 a motor part-of a truck is NOT in the small-car-motor scope
- L6.4 unknown* individuals fall into the scope classes like real constituents

### L7 — Monte-Carlo aggregation  (SPEC ONLY — red last)  [spec §8–§9]
- L7.1 single instance, point intervals → ClassCompositionStatement whole=class,
  quantity == the input (degenerate MC)
- L7.2 two instances → equal (unweighted) mean combination
- L7.4 MC config (sampleCount, percentiles) read from ontology; query override
  works
- L7.5 output is queryable with the SAME pattern as an ordinary composition
  statement (whole is a class)
- L7.6 the derived unknown portion appears in the output with its coverage

## Order of work

1. L1–L3 characterization tests (lock current behaviour) — should pass now.
2. L4 chain completeness — write red, implement `complete-chains` rule.
3. L5 reconciliation — write red, implement `reconcile-coarse-fine` rule.
4. L6 scope — write red, implement defined-class generation.
5. L7 aggregation — write red, implement the aggregator.

## Fixture naming

`fixtures/L<layer>_<n>_<slug>.ttl` — one scenario each. Expected outcome
(conforms / specific violation / derived triples) asserted in the test, with a
short comment block at the top of each fixture stating intent + expected result.

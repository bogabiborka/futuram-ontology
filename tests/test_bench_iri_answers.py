"""The answer to a class-keyed question MUST name the CLASS by IRI — never a bare
string. A model that answers "Aluminium" (the local name as free text) instead of
the resolved class IRI is WRONG, even when its number is right.

This pins that rule for the two questions added to the split domain benchmark:
  * cu_recovery_from_car                    — a names_only (membership) answer
  * crm_recovered_embedded_controllers...   — a labelled value list

Both an absolute IRI and a CURIE (prefix:Local) count as naming the class; a bare
word does not. See memory bench-answers-must-be-iris.
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
BENCH = REPO / "bench"
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

pytest.importorskip("yaml")
pytest.importorskip("rdflib")
from benchlib.agent import all_values_off_by_constant_ratio   # noqa: E402
from benchlib.cases import UNCERTAINTY_INSTRUCTION, load_testcases   # noqa: E402
from benchlib.scoring import score_answer          # noqa: E402

COMP = {c.id: c for c in load_testcases(BENCH / "testcases" / "domain-competency.yaml")}
SPARQL = {c.id: c for c in load_testcases(BENCH / "testcases" / "domain-sparql.yaml")}


# --------------------------------------------------------------------------- #
# No double-ask: a question whose verbatim text ALREADY asks for the ± must NOT
# also get the appended uncertainty instruction, and must NOT have a __valunc twin
# (that would fire a redundant second query). See cases.TestCase._asks_uncertainty.
# --------------------------------------------------------------------------- #
def test_sparql_questions_have_no_valunc_twin():
    # the 4 special SPARQL questions already request the uncertainty in their text
    assert not [cid for cid in SPARQL if cid.endswith("__valunc")]


def test_no_double_ask_when_question_already_asks_uncertainty():
    for c in list(SPARQL.values()) + list(COMP.values()):
        if c.expected.score_uncertainty and "uncertaint" in c.question.lower():
            # the instruction must NOT be appended on top of the verbatim ask
            assert UNCERTAINTY_INSTRUCTION not in c.prompt_question, c.id
            assert c.prompt_question == c.question, c.id


def test_instruction_still_appended_when_text_is_silent_on_uncertainty():
    # a competency __valunc case whose text does NOT mention uncertainty still gets it
    c = COMP["al_diesel_clioclass_2025__valunc"]
    assert c.expected.score_uncertainty
    assert UNCERTAINTY_INSTRUCTION in c.prompt_question


# --------------------------------------------------------------------------- #
# Ranking: a "rank … in decreasing order" case (ranked: true) scores the ORDER —
# right values in the wrong order is a wrong ranking. See scoring.score_answer.
# --------------------------------------------------------------------------- #
def _ranked_answer(exp, order):
    """Build a model answer carrying exp's values/labels/unc in the given index order,
    with uncertainties as a label-keyed map so they stay correct regardless of order."""
    idx = list(order)
    return {"labels": [exp.labels[i] for i in idx],
            "values": [exp.values[i] for i in idx],
            "uncertainties": {exp.labels[i]: exp.uncertainties[i] for i in idx},
            "unit": exp.unit}


def test_ranked_case_is_flagged():
    assert SPARQL["cu_by_bev_segment_2030"].expected.ranked is True
    assert SPARQL["al5xxx_by_bev_segment_2020"].expected.ranked is True
    # a plain breakdown (SI-7 list) is NOT ranked
    assert SPARQL["elements_embedded_electronics_std_bev_2030"].expected.ranked is False


def test_ranked_descending_passes():
    exp = SPARQL["cu_by_bev_segment_2030"].expected
    ans = _ranked_answer(exp, range(len(exp.values)))   # already descending in YAML
    assert score_answer(exp, ans)["correct"] is True


def test_ranked_wrong_order_fails_on_order_only():
    exp = SPARQL["cu_by_bev_segment_2030"].expected
    ans = _ranked_answer(exp, reversed(range(len(exp.values))))  # ascending
    res = score_answer(exp, ans)
    assert res["correct"] is False
    assert "not-in-decreasing-order" in res["detail"]
    # and the failure is ONLY the order — no missing/wrong-value/extra
    assert "missing" not in res["detail"] and "wrong-value" not in res["detail"]


# --------------------------------------------------------------------------- #
# cu_recovery_from_car — membership (names_only) over ChEBI base-metal classes
# --------------------------------------------------------------------------- #
def test_cu_recovery_rejects_bare_metal_names():
    exp = COMP["cu_recovery_from_car"].expected
    # the recovered metals named as free text — WRONG even though they're the right
    # metals: the answer must name the recovery-route CLASS (its base-metal IRI).
    bare = {"names": ["copper", "lead", "tin", "zinc"]}
    res = score_answer(exp, bare)
    assert res["correct"] is False
    assert "IRI" in res["detail"]


def test_cu_recovery_accepts_chebi_iris():
    exp = COMP["cu_recovery_from_car"].expected
    iris = {"names": [
        "http://purl.obolibrary.org/obo/CHEBI_28694",
        "http://purl.obolibrary.org/obo/CHEBI_25016",
        "http://purl.obolibrary.org/obo/CHEBI_27007",
        "http://purl.obolibrary.org/obo/CHEBI_27363",
    ]}
    assert score_answer(exp, iris)["correct"] is True


def test_cu_recovery_accepts_curies():
    # a CURIE (obo:CHEBI_28694) also names the class — prefix:Local is an IRI form
    exp = COMP["cu_recovery_from_car"].expected
    curies = {"names": ["obo:CHEBI_28694", "obo:CHEBI_25016",
                        "obo:CHEBI_27007", "obo:CHEBI_27363"]}
    assert score_answer(exp, curies)["correct"] is True


# --------------------------------------------------------------------------- #
# crm_recovered_embedded_controllers — labelled value list over futuram: classes
# --------------------------------------------------------------------------- #
def test_controllers_rejects_bare_element_names():
    exp = SPARQL["crm_recovered_embedded_controllers_std_bev_2025"].expected
    # right numbers, but the elements named as bare strings -> WRONG
    bare = {"labels": ["Copper", "Aluminium", "Palladium"],
            "values": [0.795906, 0.122864, 0.000432581]}
    res = score_answer(exp, bare)
    assert res["correct"] is False
    assert "IRI" in res["detail"]


def test_controllers_accepts_futuram_iris():
    # this case scores uncertainty (its text asks for it), so supply the ± too;
    # the point under test is that the IRI labels are ACCEPTED.
    exp = SPARQL["crm_recovered_embedded_controllers_std_bev_2025"].expected
    iris = {"labels": ["https://www.purl.org/futuram#Copper",
                       "https://www.purl.org/futuram#Aluminium",
                       "https://www.purl.org/futuram#Palladium"],
            "values": [0.795906, 0.122864, 0.000432581],
            "uncertainties": [0.137855, 0.0212806, 0.0000749253]}
    assert score_answer(exp, iris)["correct"] is True


def test_controllers_curies_accepted():
    exp = SPARQL["crm_recovered_embedded_controllers_std_bev_2025"].expected
    curies = {"labels": ["futuram:Copper", "futuram:Aluminium", "futuram:Palladium"],
              "values": [0.795906, 0.122864, 0.000432581],
              "uncertainties": [0.137855, 0.0212806, 0.0000749253]}
    assert score_answer(exp, curies)["correct"] is True


# --------------------------------------------------------------------------- #
# Wrong-SUBJECT via uniform scale: a multi-element breakdown where EVERY value is
# off by the SAME ratio is a wrong WHOLE/itemMass (the broad class node vs the
# vehicle-scoped instance), not a per-row arithmetic slip (every element off by
# 1.0462× = class itemMass 11.31 vs std-BEV-scope 10.82). See agent.run_one's
# wrong-subject gate.
# --------------------------------------------------------------------------- #
def test_uniform_scale_miss_is_wrong_subject():
    exp = SPARQL["crm_recovered_embedded_controllers_std_bev_2025"].expected
    # the ACTUAL failed answer from the run (values 4.62% high, labels shuffled)
    bad = {"values": [0.12853460486999999, 0.8326408573199999, 0.0004525468],
           "labels": ["futuram:Aluminium", "futuram:Copper", "futuram:Palladium"]}
    assert all_values_off_by_constant_ratio(bad, exp) is True


def test_per_row_random_miss_is_not_uniform_scale():
    exp = SPARQL["crm_recovered_embedded_controllers_std_bev_2025"].expected
    # one row wrong, the rest exact -> NOT a uniform scale -> stays plain wrong-value
    rnd = {"values": [0.90, 0.122864, 0.000432581],
           "labels": ["futuram:Copper", "futuram:Aluminium", "futuram:Palladium"]}
    assert all_values_off_by_constant_ratio(rnd, exp) is False


def test_correct_answer_is_not_flagged_as_scale_miss():
    exp = SPARQL["crm_recovered_embedded_controllers_std_bev_2025"].expected
    good = {"values": [0.795906, 0.122864, 0.000432581],
            "labels": ["futuram:Copper", "futuram:Aluminium", "futuram:Palladium"]}
    assert all_values_off_by_constant_ratio(good, exp) is False


def test_single_element_list_not_treated_as_uniform_scale():
    # a 1-element list can't establish a ratio pattern (single-element cases are
    # already handled by the _single_val branch)
    exp = COMP["al_diesel_clioclass_2025"].expected   # 1 label (Aluminium)
    one = {"values": [120.0], "labels": ["futuram:Aluminium"]}
    assert all_values_off_by_constant_ratio(one, exp) is False


# --------------------------------------------------------------------------- #
# Scalar wrong-VALUE = scope-suspect: a scalar total off by a constant factor is the
# wrong WHOLE/itemMass (the broad motor CLASS node, itemMass 38.80, vs the
# segment-C-scoped node, 34.20 → 0.9748 vs 0.86), NOT a need for a different class.
# It must classify as wrong-value on a scalar case so the re-prompt steers
# re-scoping, not class-hunting. See agent.run_one (_scope_suspect).
# --------------------------------------------------------------------------- #
def test_ree_scalar_miss_is_wrong_value_on_scalar_case():
    from benchlib.scoring import classify_error
    exp = COMP["ree_in_motor_bev_segmentC_2025"].expected
    # scalar case: one value, no per-class labels
    assert exp.values == [0.86] and not exp.labels and not exp.names_only
    # the real wrong answer from the run (the bare motor CLASS node, 0.9748)
    bad = {"value": 0.9747507, "unit": "kg",
           "labels": ["<https://www.purl.org/futuram#elvBEV_elvElectricMotor_Y2025>"]}
    sc = score_answer(exp, bad)
    assert sc["correct"] is False
    assert classify_error(sc["correct"], sc["detail"]) == "wrong-value"


# --------------------------------------------------------------------------- #
# Per-backend expected: cu_distribution has a DIFFERENT IRI identity per backend —
# fq exposes a year+vehicle-scoped scope node (fq:<comp>_in_V0301030103_Y2025),
# composition's natural identity is the component class (futuram:<comp>). Each
# backend must score against ITS expected. See cases.TestCase.expected_for.
# --------------------------------------------------------------------------- #
def test_cu_distribution_has_fq_backend_override():
    tc = COMP["cu_distribution_bev_segmentC_2025"]
    assert "fq" in tc.expected_by_backend
    # default (composition) labels are the bare component classes
    assert all("_in_V0301030103_Y2025" not in l for l in tc.expected_for("composition").labels)
    # fq override labels are the year+vehicle-scoped scope nodes
    assert all(l.endswith("_in_V0301030103_Y2025") for l in tc.expected_for("fq").labels)
    # an unknown backend falls back to the default
    assert tc.expected_for("whatever").labels == tc.expected.labels


def test_cu_distribution_scores_each_backend_against_its_own_identity():
    tc = COMP["cu_distribution_bev_segmentC_2025"]
    comp_ans = {"values": [31.185, 10.43, 3.97], "unit": "kg",
                "labels": ["futuram:elvElectricMotor", "futuram:elvEmbeddedElectronics",
                           "futuram:elvGeneralComponents"]}
    fq_ans = {"values": [31.185, 10.43, 3.97], "unit": "kg",
              "labels": ["fq:elvElectricMotor_in_V0301030103_Y2025",
                         "fq:elvEmbeddedElectronics_in_V0301030103_Y2025",
                         "fq:elvGeneralComponents_in_V0301030103_Y2025"]}
    assert score_answer(tc.expected_for("composition"), comp_ans)["correct"] is True
    assert score_answer(tc.expected_for("fq"), fq_ans)["correct"] is True
    # cross-mismatch must FAIL: a composition-style answer on the fq golden
    assert score_answer(tc.expected_for("fq"), comp_ans)["correct"] is False


# --------------------------------------------------------------------------- #
# Fraction-not-kg: a labelled answer reporting kg/kg FRACTIONS (right classes, wrong
# unit/scope) must be caught as fraction-not-kg, not mis-routed to "re-resolve a
# different class". See agent.answer_is_fraction_not_kg.
# --------------------------------------------------------------------------- #
def test_fraction_not_kg_detected_when_classes_right():
    from benchlib.agent import answer_is_fraction_not_kg
    exp = COMP["al_alloy_demand_hev_segmentB_2020"].expected
    frac = {"values": [0.1053456, 0.04081307, 0.01919285, 0.00316499], "unit": "kg/kg",
            "labels": ["futuram:castAlAlloys", "futuram:5xxxAlAlloy",
                       "futuram:6xxxAlAlloy", "futuram:2xxxAlAlloy"]}
    assert answer_is_fraction_not_kg(frac, exp) is True


def test_correct_kg_answer_not_flagged_as_fraction():
    from benchlib.agent import answer_is_fraction_not_kg
    exp = COMP["al_alloy_demand_hev_segmentB_2020"].expected
    good = {"values": [66.52, 24.98, 11.75, 1.94], "unit": "kg",
            "labels": ["futuram:castAlAlloys", "futuram:5xxxAlAlloy",
                       "futuram:6xxxAlAlloy", "futuram:2xxxAlAlloy"]}
    assert answer_is_fraction_not_kg(good, exp) is False


def test_small_value_golden_not_misread_as_fraction():
    # a case whose kg goldens are all < 1.5 must NOT be flagged (no value >=1.5 guard)
    from benchlib.agent import answer_is_fraction_not_kg
    exp = SPARQL["crm_recovered_embedded_controllers_std_bev_2025"].expected
    ans = {"values": [0.795906, 0.122864, 0.000432581], "unit": "kg/kg",
           "labels": ["futuram:Copper", "futuram:Aluminium", "futuram:Palladium"]}
    assert answer_is_fraction_not_kg(ans, exp) is False


# --------------------------------------------------------------------------- #
# Singular label: a 1-element labelled answer that uses the natural singular
# {"value": x, "label": <IRI>} must score directly — NOT be bounced as "unlabelled".
# See scoring._normalize_scalar_answer folding label -> labels.
# --------------------------------------------------------------------------- #
def test_singular_label_value_accepted():
    from benchlib.scoring import answer_shape_mismatch
    exp = COMP["al_diesel_clioclass_2025"].expected
    h = {"value": 113.05932296059001, "unit": "kg", "label": "futuram:Aluminium"}
    assert answer_shape_mismatch(h, exp) is False
    assert score_answer(exp, h)["correct"] is True


def test_unlabelled_values_still_flagged():
    from benchlib.scoring import answer_shape_mismatch
    exp = COMP["al_diesel_clioclass_2025"].expected
    assert answer_shape_mismatch({"values": [113.06], "unit": "kg"}, exp) is True


def test_singular_bare_string_label_still_rejected():
    # folding label->labels must NOT let a bare (non-IRI) label through
    exp = COMP["al_diesel_clioclass_2025"].expected
    assert score_answer(exp, {"value": 113.06, "unit": "kg", "label": "Aluminium"})["correct"] is False


# --------------------------------------------------------------------------- #
# Scalar fraction-not-kg: a SCALAR-total question answered as a kg/kg fraction must
# be caught by the fraction gate (multiply by itemMass), NOT mis-labelled as a
# "wrong-subject / constituent fractions are right" error — a scalar has no
# constituents. See agent.answer_is_fraction_not_kg scalar branch.
# --------------------------------------------------------------------------- #
def test_scalar_fraction_not_kg_detected():
    from benchlib.agent import answer_is_fraction_not_kg
    exp = COMP["crm_total_bev_segmentD_2020"].expected
    assert exp.labels == [] and not exp.names_only      # scalar total
    h = {"value": 0.16025978, "unit": "kg/kg", "labels": ["futuram:V0301030104_Y2020"]}
    assert answer_is_fraction_not_kg(h, exp) is True


def test_scalar_correct_kg_not_flagged():
    from benchlib.agent import answer_is_fraction_not_kg
    exp = COMP["crm_total_bev_segmentD_2020"].expected
    assert answer_is_fraction_not_kg({"value": 349.27, "unit": "kg"}, exp) is False


# --------------------------------------------------------------------------- #
# Ungrounded check looks at the CALLING HISTORY: a total the model assembled by
# SUMMING the per-element kg it actually retrieved is GROUNDED (not "fabricated") —
# only a number with NO basis in the results it received is flagged. A genuinely
# invented number still flags. See scoring.ungrounded_answer_numbers.
# --------------------------------------------------------------------------- #
def test_ungrounded_accepts_sum_of_retrieved_numbers():
    from benchlib.scoring import ungrounded_answer_numbers
    per_el = [113.05, 49.73, 80.0, 60.0, 46.88]          # sum 349.66
    msgs = [{"role": "tool",
             "content": f'{{"results":{{"bindings":[{{"kg":{{"value":"{v}"}}}}]}}}}'}
            for v in per_el]
    assert ungrounded_answer_numbers({"value": 349.66}, msgs) == []   # grounded sum
    assert ungrounded_answer_numbers({"value": 113.05}, msgs) == []   # single value


def test_ungrounded_still_flags_fabricated_number():
    from benchlib.scoring import ungrounded_answer_numbers
    msgs = [{"role": "tool",
             "content": '{"results":{"bindings":[{"kg":{"value":"113.05"}}]}}'}]
    assert ungrounded_answer_numbers({"value": 999.99}, msgs) == [999.99]


def test_ungrounded_no_history_does_not_flag():
    from benchlib.scoring import ungrounded_answer_numbers
    assert ungrounded_answer_numbers({"value": 349.66}, []) == []

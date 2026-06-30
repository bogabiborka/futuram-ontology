from __future__ import annotations

from .helpers import (
    all_values_off_by_constant_ratio,
    answer_is_fraction_not_kg,
    _fetched_resolve_class,
)
from .scoring import (
    _answer_label_items,
    _answer_numbers,
    _iri_key,
    _is_iri,
    answer_has_string_labels,
    answer_missing_labels,
    answer_missing_uncertainties,
    answer_shape_mismatch,
    classify_error,
    score_answer,
    ungrounded_answer_numbers,
)

_IN_DATA_REASSURE = (
    " (The correct answer IS in this dataset — this rejection means your query/answer "
    "needs fixing, NOT that the data is missing. Keep going until you have it.)")


def build_reprompt(
    attempt,
    expected,
    backend_id: str,
    skill_text,
    verbose: bool,
    frac_retries: int,
    resolve_retries: int,
    shape_retries: int,
    label_retries: int,
    missing_label_retries: int,
    unc_retries: int,
    recompute_retries: int,
    order_retries: int,
    subject_retries: int,
    repeat_retries: int,
    rejected_signatures: set,
) -> dict:
    """Evaluate one completed attempt against the corrective-retry predicates and
    return a result dict with keys:

    - ``accept``: bool — the attempt should be accepted as the chosen answer
    - ``reprompt_note``: str — the re-prompt message to prepend (empty if accept)
    - ``decrement_attempt``: bool — True when this is a corrective re-prompt that
      should NOT spend an attempt slot
    - ``frac_retries``: updated counter
    - ``resolve_retries``: updated counter
    - ``shape_retries``: updated counter
    - ``label_retries``: updated counter
    - ``missing_label_retries``: updated counter
    - ``unc_retries``: updated counter
    - ``recompute_retries``: updated counter
    - ``order_retries``: updated counter
    - ``subject_retries``: updated counter
    - ``repeat_retries``: updated counter
    - ``rejected_signatures``: updated set
    """

    def _answer_signature(ans):
        if not ans:
            return None
        labs = tuple(sorted(_iri_key(x).lower() for x in _answer_label_items(ans)))
        nums = tuple(sorted(round(n, 4) for n in _answer_numbers(ans)))
        return (labs, nums)

    ans = attempt["answer"]

    _ans_sig = _answer_signature(ans)
    _is_repeat = _ans_sig is not None and _ans_sig in rejected_signatures
    _repeat_prefix = (
        "YOU ALREADY HANDED IN THIS EXACT ANSWER and it was rejected — "
        "re-submitting the same number(s)/class(es) will keep failing. You MUST "
        "change your approach this time, not repeat yourself. " if _is_repeat else "")
    if _ans_sig is not None:
        rejected_signatures = set(rejected_signatures)
        rejected_signatures.add(_ans_sig)

    state = {
        "frac_retries": frac_retries,
        "resolve_retries": resolve_retries,
        "shape_retries": shape_retries,
        "label_retries": label_retries,
        "missing_label_retries": missing_label_retries,
        "unc_retries": unc_retries,
        "recompute_retries": recompute_retries,
        "order_retries": order_retries,
        "subject_retries": subject_retries,
        "repeat_retries": repeat_retries,
        "rejected_signatures": rejected_signatures,
    }

    def _ret_continue(note, counter_key):
        s = dict(state)
        s[counter_key] += 1
        s["accept"] = False
        s["reprompt_note"] = note
        s["decrement_attempt"] = True
        return s

    def _ret_accept():
        s = dict(state)
        s["accept"] = True
        s["reprompt_note"] = ""
        s["decrement_attempt"] = False
        return s

    if answer_is_fraction_not_kg(ans, expected) and frac_retries < 2:
        _scalar_total = not expected.labels and not expected.names_only
        if _scalar_total:
            note = (_repeat_prefix +
                "ANSWER REJECTED — you reported a kg/kg FRACTION, not absolute kg. "
                "This question wants ONE total in kg. Inside ONE query: bind the "
                "WHOLE's fq:itemMass and multiply it by the summed fraction(s) so "
                "the result is absolute kg (SELECT (SUM(?amount) * ?itemMass AS "
                "?totalKg) or ?itemMass * ?fraction). If a year is named, bind the "
                "whole's YEAR class. Report that one kg number (with absolute ± in "
                "kg if asked). Do NOT report the fraction." + _IN_DATA_REASSURE)
        else:
            note = (_repeat_prefix +
                "ANSWER REJECTED — you reported the kg/kg FRACTION, not absolute kg. "
                "Your CLASSES are correct — do NOT change them and do NOT re-resolve "
                "to different classes. Two fixes, both inside ONE query: (1) read the "
                "constituents off the WHOLE the question pins (its own fq:itemMass), "
                "not inside a sub-component scope — a sub-scope fraction is relative "
                "to that sub-scope, not the whole; (2) multiply fq:amount * fq:itemMass "
                "so each value is absolute kg (SELECT (?amount * ?itemMass AS ?kg)). "
                "For the ± give the ABSOLUTE uncertainty in kg (relativeUncertainty * "
                "kg), not the relative fraction. Re-emit the SAME classes with kg values."
                + _IN_DATA_REASSURE)
        if verbose:
            print(f"      [{backend_id}] corrective re-prompt: fraction→kg "
                  f"({'scalar total' if _scalar_total else 'keep classes'}; "
                  f"no attempt spent)", flush=True)
        return _ret_continue(note, "frac_retries")

    if (skill_text and not _fetched_resolve_class(attempt.get("messages", []))
            and resolve_retries < 2
            and not score_answer(expected, ans)["correct"]):
        note = (_repeat_prefix +
            "ANSWER REJECTED — you did not resolve the class with the required "
            "method. The question names/describes a thing that could map to "
            "SEVERAL classes holding DIFFERENT values (a broad roll-up vs the "
            "specific member the question pins). You MUST call "
            "get_skill(\"resolve-class\") and follow it to pick the "
            "best-fitting class — reading each candidate's rdfs:comment and "
            "choosing the more granular class when the question pins something "
            "more specific (or the broader roll-up when it is general) — then "
            "re-run your query against THAT class and re-answer."
            + _IN_DATA_REASSURE)
        if verbose:
            print(f"      [{backend_id}] corrective re-prompt: resolve-class "
                  f"method not used → enforce it (does not spend an attempt)",
                  flush=True)
        return _ret_continue(note, "resolve_retries")

    if answer_shape_mismatch(ans, expected) and shape_retries < 2:
        _vlist = ans.get("values")
        _has_vals = isinstance(_vlist, (list, tuple)) and len(_vlist) > 0
        _has_labs = bool(_answer_label_items(ans))
        _scalar_v = ans.get("value")
        _no_number = (not isinstance(_scalar_v, (int, float))
                      and not (isinstance(_vlist, (list, tuple))
                               and any(isinstance(x, (int, float)) for x in _vlist)))
        _exp_scalar_total = (not expected.labels and not expected.names_only
                             and len(expected.values or []) == 1)
        if _no_number:
            _shape_problem = ("your answer has NO NUMBER (value is null / values "
                              "is empty). You never reported a computed amount. "
                              "Run the aggregation query and put its numeric "
                              "result in the answer")
        elif _exp_scalar_total and _has_vals and len(_vlist) > 1:
            _shape_problem = ("this question wants ONE total number, but you "
                              "returned a LIST of several values. Your per-item "
                              "values look right — just ADD THEM UP. Take the "
                              "EXACT query that produced this list (same whole, "
                              "same scope, same year) and wrap its amount in "
                              "SUM: SELECT (SUM(?amount * ?itemMass) AS ?total). "
                              "Do NOT switch to a different subject/class and do "
                              "NOT re-resolve — keep the one you just used, only "
                              "collapse the rows into one total. For the ± use "
                              "the RSS (sqrt of the sum of squared per-item "
                              "absolute uncertainties), computed in the query")
        elif "value" in ans and not _has_vals:
            _shape_problem = "you used the singular \"value\" field"
        elif _has_labs and not _has_vals:
            _shape_problem = ("you gave \"labels\" (the classes) but \"values\" "
                              "is EMPTY — you reported NO numbers. You must query "
                              "each class's amount and report a number per label")
        else:
            _shape_problem = ("your \"values\" and \"labels\" are missing or not "
                              "the same length")
        _scalar_case = not expected.labels and not expected.names_only
        _shape_template = (
            ("  ANSWER: {\"value\": <num>, \"unit\": \"kg\""
             + (", \"uncertainty\": <num>" if expected.score_uncertainty else "")
             + "}\n  (a SINGLE total number — no per-class labels)\n")
            if _scalar_case else
            ("  ANSWER: {\"values\":[<num>,...], \"labels\":[\"<classIRI>\",...], "
             "\"unit\":\"kg\""
             + (", \"uncertainties\":[<num>,...]" if expected.score_uncertainty else "")
             + "}\n"
             "Rules: one entry per class; values[], labels[]"
             + (", uncertainties[]" if expected.score_uncertainty else "")
             + " ALL the same length, aligned position-by-position; every "
             "label is the CLASS IRI you resolved (full "
             "<https://www.purl.org/futuram#...> or prefixed futuram:...). Do "))
        _reassure = ""
        if _no_number or (_has_labs and not _has_vals):
            _reassure = (
                " The answer IS in the dataset — do NOT give up or report a "
                "membership/labels-only list as a \"proxy\". Each class you found "
                "carries its amount on the SAME node as its fq:itemMass: read "
                "fq:contains [ fq:constituent <theConstituentIRI> ; fq:amount ?f ] "
                "(and fq:relativeUncertainty if ± is asked) and compute the kg "
                "in-query (?itemMass * ?f). If a query came back empty, the BINDING "
                "was wrong (wrong parent/constituent/slice), not the data — fix the "
                "pattern and re-run. An empty/labels-only answer scores ZERO.")
        note = (_repeat_prefix +
            f"ANSWER REJECTED — wrong shape: {_shape_problem}.{_reassure} This "
            "question is scored ONLY in this exact form and any other shape fails "
            "automatically:\n"
            + _shape_template
            + ("Compute the single total in ONE query and put its number in "
               "\"value\"."
               if _scalar_case else
               "Do NOT use the singular \"value\" field. Do NOT give a number "
               "without its class IRI. Report each value with its class IRI in "
               "exactly this shape."))
        if verbose:
            print(f"      [{backend_id}] corrective re-prompt: answer shape "
                  f"(use values[]+labels[]) → does not spend an attempt", flush=True)
        return _ret_continue(note, "shape_retries")

    if answer_has_string_labels(ans) and label_retries < 2:
        bad = [l for l in _answer_label_items(ans) if not _is_iri(str(l))]
        note = (_repeat_prefix +
            "ANSWER REJECTED — these labels are plain names, not class IRIs: "
            f"{bad}. A plain-name label fails automatically. Replace EACH with "
            "the FULL class IRI you resolved from the data (an <https://...#...> "
            "IRI, or its prefixed form), aligned position-by-position with "
            "\"values\". Re-run the query if needed to read the constituent "
            "class IRIs, then re-emit the SAME values with their IRI labels.")
        if verbose:
            print(f"      [{backend_id}] corrective re-prompt: string labels → "
                  f"ask for class IRIs (does not spend an attempt)", flush=True)
        return _ret_continue(note, "label_retries")

    if (expected.labels and not expected.names_only
            and answer_missing_labels(ans) and missing_label_retries < 2):
        got_nums = _answer_numbers(ans)
        note = (_repeat_prefix +
            "ANSWER REJECTED — your answer is UNGROUNDED: you reported "
            f"number(s) {got_nums} without any class IRI identifying WHAT "
            "they are. Every value must be labelled by the full class IRI "
            "(<https://...#...> or prefixed form) you resolved from the data. "
            "Re-run the query with SELECT ?classIRI ... to read the IRI, then "
            "put it in \"labels\" aligned position-by-position with \"values\". "
            "A number without its IRI is automatically wrong.")
        if verbose:
            print(f"      [{backend_id}] corrective re-prompt: missing IRI label → "
                  f"attach class IRI (does not spend an attempt)", flush=True)
        return _ret_continue(note, "missing_label_retries")

    if (expected.score_uncertainty
            and answer_missing_uncertainties(ans) and unc_retries < 2):
        note = (_repeat_prefix +
            "ANSWER REJECTED — your answer has no \"uncertainties\", but this "
            "question REQUIRES a ± for every quantity. An answer without "
            "automatically. Add an \"uncertainties\" array aligned position-by-"
            "position with \"values\" (same length), absolute ± in the SAME "
            "unit. The uncertainty is available in the data on the SAME amount "
            "node you already read the value from — inspect that node's "
            "predicates (search the docs/VoID if unsure which one), SELECT it "
            "alongside each amount, and do NOT invent it. Re-emit the SAME "
            "values WITH their aligned uncertainties.")
        if verbose:
            print(f"      [{backend_id}] corrective re-prompt: missing "
                  f"uncertainty → ask for ± (does not spend an attempt)", flush=True)
        return _ret_continue(note, "unc_retries")

    if (expected.score_uncertainty and not answer_missing_uncertainties(ans)
            and unc_retries < 2):
        _sc = score_answer(expected, ans)
        _detail = _sc.get("detail") or ""
        # Only fire the RSS re-prompt when values are right but uncertainty alone is
        # wrong — i.e. no missing labels and no wrong values, just unc-wrong.
        if (not _sc["correct"] and "unc-wrong" in _detail
                and "missing" not in _detail and "wrong-value" not in _detail):
            note = (_repeat_prefix +
                "ANSWER REJECTED — your uncertainty is WRONG. You reported a ± value "
                "but it does not match the expected result. The most common cause: "
                "summing absolute uncertainties linearly (σ_total = Σσ_i) instead of "
                "in quadrature (σ_total = √(Σσ_i²)). When combining INDEPENDENT "
                "quantities the uncertainties must be RSS-combined — use "
                "`afn:sqrt(SUM(?sigma * ?sigma))` in your query, where "
                "`?sigma = ?relativeUncertainty * ?kg` for each constituent. "
                "Re-run the query with the correct propagation and re-answer."
                + _IN_DATA_REASSURE)
            if verbose:
                print(f"      [{backend_id}] corrective re-prompt: wrong uncertainty "
                      f"value → RSS propagation (does not spend an attempt)", flush=True)
            return _ret_continue(note, "unc_retries")

    ungrounded = ungrounded_answer_numbers(ans, attempt.get("messages", []))
    if ungrounded and recompute_retries < 2:
        note = (_repeat_prefix +
            "ANSWER REJECTED — ungrounded number(s). These ANSWER values appear "
            f"in NO SPARQL result you ran: {', '.join(f'{u:g}' for u in ungrounded)}. "
            "A hand-computed number fails automatically. Do NOT do arithmetic in "
            "your head — fractions are tiny (scientific notation) and drift when "
            "multiplied by hand. Make the engine compute it inside the query "
            "with a projection expression (SELECT (?a * ?b AS ?result) ...) and "
            "report the engine's result values directly.")
        if verbose:
            print(f"      [{backend_id}] corrective re-prompt: ungrounded numbers → "
                  f"compute in SPARQL (does not spend an attempt)", flush=True)
        return _ret_continue(note, "recompute_retries")

    if expected.ranked and order_retries < 2:
        _scord = score_answer(expected, ans)
        if (not _scord["correct"]
                and "not-in-decreasing-order" in (_scord["detail"] or "")
                and "missing" not in _scord["detail"]
                and "wrong-value" not in _scord["detail"]
                and "extra" not in _scord["detail"]):
            note = (_repeat_prefix +
                "ANSWER REJECTED — wrong ORDER. Your values and labels are right, "
                "but this question asks to rank them IN DECREASING ORDER and your "
                "rows are not sorted high-to-low. Re-emit the SAME values, "
                "uncertainties and labels, but ordered so each value is ≥ the next "
                "(largest first, smallest last) — use ORDER BY DESC in the query "
                "and list the answer arrays in that order. Change NOTHING else.")
            if verbose:
                print(f"      [{backend_id}] corrective re-prompt: ranked order "
                      f"wrong → re-sort descending (does not spend an attempt)",
                      flush=True)
            return _ret_continue(note, "order_retries")

    if expected.route_rows and subject_retries < 2:
        _scr = score_answer(expected, ans)
        _rdet = _scr.get("detail") or ""
        if not _scr["correct"] and "route-rows-missing" in _rdet:
            # Values and uncertainties are fine; only the route table is incomplete.
            # The model gave a dict (1 route per element) instead of a list of
            # (element, base_metal, route) triples covering ALL metal-wheel contexts.
            note = (_repeat_prefix +
                "ANSWER REJECTED — your routes are INCOMPLETE. The question asks for "
                "ALL POTENTIAL recycling routes (the full metal-wheel table), but you "
                "gave only ONE route per element. Each element appears in MULTIPLE "
                "recycling contexts (one per base metal it co-occurs with). "
                "Your \"routes\" field must be a LIST of triples — one entry per "
                "(element, base_metal, route) pair — not a dict. Format:\n"
                "  \"routes\": [{\"element\": \"<iri>\", \"base_metal\": \"<baseMetal>\", "
                "\"route\": \"<proc>\"}, ...]\n"
                "Re-run the metal-wheel query returning DISTINCT (?key, ?baseMetal, ?route) "
                "and emit ALL rows. Your values and uncertainties are CORRECT — do NOT "
                "change them, only fix the routes."
                + _IN_DATA_REASSURE)
            if verbose:
                print(f"      [{backend_id}] corrective re-prompt: route-rows-missing "
                      f"→ full metal-wheel table as list (does not spend an attempt)",
                      flush=True)
            return _ret_continue(note, "subject_retries")

    _scsubj = score_answer(expected, ans)
    _subj_cat = classify_error(_scsubj["correct"], _scsubj["detail"])
    _exp_n = len(expected.values or [])
    _single_val = (
        (expected.labels and len(expected.labels) == 1
         and not expected.names_only)
        or (not expected.labels and not expected.names_only
            and _exp_n == 1))
    _uniform_scale = (_subj_cat == "wrong-value"
                      and all_values_off_by_constant_ratio(ans, expected))
    _is_wrong_subject = (_subj_cat == "wrong-class"
                         or _subj_cat == "wrong-value"
                         or _uniform_scale)
    _scope_suspect = (_subj_cat == "wrong-value"
                      and (_uniform_scale or _single_val))
    if _is_wrong_subject and subject_retries < 5:
        _repeated = _is_repeat
        if _repeated:
            repeat_retries += 1
            note = (
                "ANSWER REJECTED — you already submitted THIS EXACT answer and "
                "it was rejected as the wrong subject. Resubmitting the same "
                "class will keep failing. You MUST query a DIFFERENT class this "
                "time. A MORE GRANULAR (more specific) or a BROADER class than "
                "the one you used may be what the question means — re-run "
                "get_skill(\"resolve-class\"), read the candidates' rdfs:comment "
                "to tell a roll-up from a specific class, and pick a different "
                "one." + _IN_DATA_REASSURE)
        elif _scope_suspect:
            _uni = (" Every value is off by the SAME factor, so"
                    if _uniform_scale else
                    " The number is off by a constant factor, so")
            note = (
                "ANSWER REJECTED — wrong SUBJECT (most likely the wrong WHOLE, not "
                "the wrong component)." + _uni + " your constituent fraction(s) "
                "are right but you multiplied by the WRONG itemMass — you queried "
                "the broad/aggregate component CLASS instead of that component "
                "SCOPED to the specific whole the question pins. DO NOT go looking "
                "for a different component class or a non-existent variant: it is "
                "almost certainly the SAME component, just scoped wrong. If the "
                "question names a specific whole (a particular product), bind the "
                "component as the in-context node that is `futuram:partOf` that "
                "whole and read ITS `fq:itemMass` — not the bare component class's "
                "global-average itemMass. get_skill(\"component-in-whole-fq\") for "
                "the exact scope-node shape, then re-answer." + _IN_DATA_REASSURE)
        elif _subj_cat == "wrong-value" and not _scope_suspect:
            note = (
                "ANSWER REJECTED — wrong VALUES. Your labels are correct but the "
                "numbers are off. Common causes: (1) your scope is too broad — a "
                "FILTER on an IRI substring or a subClassOf* traversal is matching "
                "MORE nodes than the question pins (e.g. multiple variants or "
                "years); anchor with `futuram:partOf <VehicleIRI>` instead. "
                "(2) you queried individual sub-component leaf nodes but the fq "
                "endpoint already pre-aggregates those into the PARENT scope node "
                "— query the parent directly (one level up). Re-examine the scope "
                "of your query and re-answer." + _IN_DATA_REASSURE)
        else:
            note = (
                "ANSWER REJECTED — wrong SUBJECT. Your answer is well-formed but "
                "the class you resolved does not match what the question asks "
                "about. A MORE GRANULAR (more specific) or a "
                "BROADER class than the one you used may be what is meant. Re-run "
                "get_skill(\"resolve-class\"), read each candidate's rdfs:comment "
                "to tell a roll-up from a specific class, and query a DIFFERENT "
                "class than before, then re-answer." + _IN_DATA_REASSURE)
        if verbose:
            _cat_label = ("WRONG VALUE" if _subj_cat == "wrong-value" and not _scope_suspect
                          else "WRONG SUBJECT")
            print(f"      [{backend_id}] corrective re-prompt: {_cat_label}"
                  f"{' (REPEAT)' if _repeated else ''} "
                  f"(retry {subject_retries + 1}/5, does not spend an attempt)",
                  flush=True)
        s = dict(state)
        s["subject_retries"] = subject_retries + 1
        s["repeat_retries"] = repeat_retries
        s["accept"] = False
        s["reprompt_note"] = note
        s["decrement_attempt"] = True
        return s

    return _ret_accept()

"""Answer parsing + scoring, and the corrective-retry predicates. Parse the LAST
`ANSWER: <json>` line, compare numerically (relative tolerance + unit normalisation).
PURE — no LLM-facing prompt strings (the re-prompt text lives in agent.py)."""
from __future__ import annotations

import json
import math
import re

from .cases import Expected
from .units import canonical

_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


def parse_answer(text: str) -> dict | None:
    """Pull the structured answer out of the model's final message."""
    # Prefer an explicit ANSWER: {json} line.
    matches = re.findall(r"ANSWER:\s*(\{.*\}|\[.*\])", text, re.DOTALL)
    for m in reversed(matches):
        try:
            obj = json.loads(m)
            return obj if isinstance(obj, dict) else {"values": obj}
        except json.JSONDecodeError:
            continue
    # Fallback: a bare "ANSWER: 12.3 kg/kg" line.
    m = re.search(rf"ANSWER:\s*({_NUM})\s*([A-Za-z%/\-]*)", text)
    if m:
        return {"value": float(m.group(1)), "unit": m.group(2)}
    return None


def _expected_canon(exp: Expected) -> list[tuple[float, str]]:
    return [canonical(v, exp.unit) for v in exp.values]


def _num_or_none(x):
    """Coerce x to float, or None if it is null / non-numeric. The model can emit a
    null or junk `value` (e.g. {"value": null}); that must score as "no value", not
    crash the whole run with a TypeError."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _answer_canon(ans: dict) -> tuple[list[tuple[float, str]], str]:
    unit = str(ans.get("unit", ""))
    if "values" in ans and ans["values"] is not None:
        vals = ans["values"]
        if not isinstance(vals, (list, tuple)):
            vals = [vals]
        # values may be a list of numbers, or of {value,unit,label} dicts
        out = []
        for v in vals:
            if isinstance(v, dict):
                n = _num_or_none(v.get("value", v.get("amount")))
                if n is not None:
                    out.append(canonical(n, str(v.get("unit", unit))))
            else:
                n = _num_or_none(v)
                if n is not None:
                    out.append(canonical(n, unit))
        return out, unit
    if "value" in ans:
        n = _num_or_none(ans["value"])
        return ([canonical(n, unit)] if n is not None else []), unit
    return [], unit


def _is_iri(s: str) -> bool:
    """True only for an RDF resource identifier — a full IRI or a CURIE
    (prefix:Local). A bare word ('aluminium') is rejected: an answer must name
    the CLASS it resolved from the data, not free text."""
    s = str(s).strip().strip("<>")
    if "://" in s:
        return True
    # prefix:Local — a colon with non-space on both sides, not a bare 'http'
    return bool(re.match(r"^[A-Za-z][\w.\-]*:[A-Za-z0-9_\-%.]+$", s))


def _iri_key(s: str) -> str:
    """Identity of a resource for set comparison: its IRI local-name (after # or /
    or a CURIE colon). Case-SENSITIVE — Aluminium and aluminium are different
    resources; we do not lowercase, because that is how string-acceptance crept in."""
    s = str(s).strip().strip("<>")
    if "://" in s:
        return re.split(r"[#/]", s)[-1]
    if ":" in s:
        return s.split(":", 1)[1]
    return s


def _answer_label_items(ans: dict) -> list[str]:
    """The label/identifier items an answer carries (its `labels`, `names`, or the
    label on each value dict) — NOT the numeric values."""
    out = []
    if isinstance(ans.get("labels"), list):
        out += [str(x) for x in ans["labels"]]
    if isinstance(ans.get("names"), list):
        out += [str(x) for x in ans["names"]]
    for v in ans.get("values", []) or []:
        if isinstance(v, dict):
            nm = v.get("iri") or v.get("class") or v.get("label")
            if nm:
                out.append(str(nm))
    return out


def answer_has_string_labels(ans: dict | None) -> bool:
    """True if the answer carries label items but at least one is a bare string,
    not a class IRI. Used to give the model a fair retry: it named the right
    things but didn't identify them as classes."""
    if not ans:
        return False
    items = _answer_label_items(ans)
    return bool(items) and any(not _is_iri(x) for x in items)


def answer_missing_labels(ans: dict | None) -> bool:
    """True if the answer has numeric value(s) but NO (or blank) labels — the
    class IRI was never attached, so values score as `(unnamed)`. Distinct from
    string labels (present but not IRIs): here labels are absent/empty."""
    if not ans:
        return False
    if not _answer_numbers(ans):
        return False
    items = _answer_label_items(ans)
    # no label items at all, or every label item is blank
    return not items or all(not str(x).strip() for x in items)


def answer_missing_uncertainties(ans: dict | None) -> bool:
    """True if the answer has numeric value(s) but carries NO uncertainties — used to
    nudge a `__valunc` question that forgot the ±. An empty/all-None uncertainty
    payload counts as missing (a present-but-blank ± is no answer to the ± ask)."""
    if not ans or not _answer_numbers(ans):
        return False
    u = ans.get("uncertainties")
    if u is None:
        u = ans.get("uncertainty")
    if u is None:
        return True
    vals = u.values() if isinstance(u, dict) else (u if isinstance(u, (list, tuple)) else [u])
    # missing if there is nothing, or every entry is None/blank
    return not vals or all(x is None or str(x).strip() == "" for x in vals)


def _normalize_scalar_answer(ans: dict | None) -> dict | None:
    """Fold a SCALAR answer ({"value": x[, "uncertainty": u]} possibly with a single
    "labels":[iri]) into the list form ({"values":[x], "uncertainties":[u]}) so the
    labelled scorer reads it. A single number with one label IS a valid 1-element
    answer — it must score on its merits, not be rejected/mangled for using the
    singular field. No-op if there is no scalar `value` or `values` already exists."""
    if not isinstance(ans, dict):
        return ans
    has_values = isinstance(ans.get("values"), (list, tuple)) and len(ans["values"]) > 0
    if has_values or "value" not in ans:
        return ans
    v = ans.get("value")
    if not isinstance(v, (int, float)):
        return ans                       # value:null etc. — leave for the no-number path
    out = dict(ans)
    out["values"] = [v]
    out.pop("value", None)
    if "uncertainty" in out and "uncertainties" not in out:
        u = out.pop("uncertainty")
        out["uncertainties"] = [u]
    # Fold a SINGULAR `label` -> `labels:[label]` too. A 1-element answer that names
    # its class with the natural singular `label` (alongside the singular `value`) is
    # well-formed — without this the labelled scorer sees `values` but no `labels` and
    # rejects it as "unlabelled", forcing a needless re-emit as a list.
    if "labels" not in out:
        lab = out.pop("label", None)
        if lab not in (None, ""):
            out["labels"] = [lab]
    return out


def _answer_has_no_number(ans: dict | None) -> bool:
    """True if the answer carries NO usable number at all — `value` is null/absent AND
    `values` is empty/all-None. A degenerate 'no number' answer that nonetheless parsed
    to a dict (so it isn't caught as a missing ANSWER line) — must be re-prompted, not
    accepted."""
    if not ans:
        return True
    v = ans.get("value")
    if isinstance(v, (int, float)):
        return False
    vals = ans.get("values")
    if isinstance(vals, (list, tuple)) and any(
            isinstance(x, (int, float)) for x in vals):
        return False
    return True


def answer_shape_mismatch(ans: dict | None, exp: "Expected") -> bool:
    """True if the answer's SHAPE can't be scored: a scalar `value`, or `values` with
    no aligned `labels`, when the case wants one value PER class IRI; OR an answer that
    parsed but carries NO number (value:null / values:[]). Reject + re-prompt rather
    than silently accept a numberless answer."""
    if not ans or exp is None:
        return False
    # NO NUMBER AT ALL (value:null / values:[]) — applies to EVERY case shape,
    # including a scalar-total (label-less) case. This parsed to a dict so it escaped
    # the "no ANSWER line" path, but it answers nothing — re-prompt to produce a number.
    if not exp.names_only and _answer_has_no_number(ans):
        return True
    # a scalar {"value": x, ["uncertainty": u]} with one label IS scoreable (the
    # scorer normalises it) — do NOT flag it as a shape mismatch just for the singular
    # field, or we reject a correct 1-element answer and risk a bad re-prompt.
    ans = _normalize_scalar_answer(ans)
    # SCALAR-TOTAL case expected (one number, no per-class labels) but the answer is a
    # MULTI-element list — the model listed the parts instead of SUMMING them to the
    # one total the question asks for. Re-prompt to sum into a single number. (A
    # 1-element list is fine — it's the same as the scalar.)
    _exp_scalar = (not exp.names_only and not exp.labels
                   and len(exp.values or []) == 1)
    _ans_vals = ans.get("values")
    if (_exp_scalar and isinstance(_ans_vals, (list, tuple)) and len(_ans_vals) > 1):
        return True
    if exp.names_only or not exp.labels:
        return False
    has_values_list = isinstance(ans.get("values"), (list, tuple)) and len(ans["values"]) > 0
    labs = _answer_label_items(ans)
    # scalar `value` used instead of `values[]`, OR values present but unlabelled,
    # OR a values/labels length mismatch — none of which the labelled scorer reads.
    if "value" in ans and not has_values_list:
        return True
    if has_values_list and (not labs or len(labs) != len(ans["values"])):
        return True
    # LABELS BUT NO NUMBERS — the model gave class IRIs with an empty `values: []`
    # (and no scalar `value`). It identified the right things but never reported their
    # amounts: a degenerate, unscoreable shape that otherwise falls through to
    # no-answer and is accepted. Re-prompt it to fill in the numbers.
    if labs and not has_values_list and "value" not in ans:
        return True
    return False


def _answer_numbers(ans: dict | None) -> list[float]:
    """Every numeric value in the answer (values list, or a single value)."""
    if not ans:
        return []
    out = []
    raw = ans.get("values")
    if raw is None and "value" in ans:
        raw = [ans["value"]]
    for v in (raw or []):
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return out


# every number that appears in a SPARQL result text the model received
_RESULT_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def ungrounded_answer_numbers(ans: dict | None, messages: list, *,
                              rtol: float = 2e-3) -> list[float]:
    """Answer numbers that DO NOT trace to the model's SPARQL CALLING HISTORY.
    Guardrail against hand-arithmetic — but a value is GROUNDED if it is either a
    number the engine returned OR a plain COMBINATION (a sum) of numbers the engine
    returned across the queries it ran. So a total a model assembled by summing the
    per-element kg it actually retrieved is NOT flagged (only a number with no basis
    in the history is). We still PREFER the model compute the sum in-query (the
    re-prompt says so), but we do not call a result-derived total 'fabricated'."""
    nums = _answer_numbers(ans)
    if not nums:
        return []
    # Collect the numeric pool PER tool message (one query's result block), so we can
    # recognise both a single returned number and a per-query subtotal.
    per_msg: list[list[float]] = []
    for m in (messages or []):
        if m.get("role") == "tool" and m.get("content"):
            vals = []
            for tok in _RESULT_NUM.findall(str(m["content"])):
                try:
                    vals.append(float(tok))
                except ValueError:
                    pass
            if vals:
                per_msg.append(vals)
    flat = [x for msg in per_msg for x in msg]
    if not flat:
        return []
    apool = sorted({abs(x) for x in flat})

    def _match(av: float, p: float) -> bool:
        return p != 0 and abs(av - p) <= rtol * max(av, abs(p))

    # Candidate "totals" the model could legitimately have assembled from results:
    #  - any single returned number (the engine's value);
    #  - the SUM of all numbers within one result block (a multi-row query summed);
    #  - the SUM of the per-message subtotals (it ran one query per item, then added);
    #  - the PRODUCT of any two returned numbers (fraction × itemMass is canonical).
    msg_sums = [sum(abs(x) for x in msg) for msg in per_msg]
    combos = list(apool)
    combos += msg_sums
    combos.append(sum(msg_sums))
    # pairwise products: covers frac * itemMass computed outside the query
    if len(apool) <= 50:  # guard against huge result sets
        for i, a in enumerate(apool):
            for b in apool[i:]:
                combos.append(a * b)
    # bounded subset-sum over the per-message subtotals (model summed SOME of the
    # queries it ran) — only for a small number of messages, to stay cheap.
    if 1 < len(msg_sums) <= 16:
        from itertools import combinations
        for r in range(2, len(msg_sums) + 1):
            for sub in combinations(msg_sums, r):
                combos.append(sum(sub))

    bad = []
    for v in nums:
        av = abs(v)
        if av == 0.0:
            continue
        if any(_match(av, c) for c in combos):
            continue                          # in history, or a sum of history values
        bad.append(v)
    return bad


def _answer_names(ans: dict) -> list[str]:
    """Raw items the answer lists (labels of value dicts, or a bare names list)."""
    out = []
    for key in ("names", "values", "labels"):
        v = ans.get(key)
        if not v:
            continue
        for item in v:
            if isinstance(item, dict):
                nm = item.get("iri") or item.get("class") or item.get("label") \
                    or item.get("name") or item.get("constituent")
                if nm:
                    out.append(str(nm))
            else:
                out.append(str(item))
    return out


def score_answer(exp: Expected, ans: dict | None, rtol: float = 0.02,
                 atol: float = 1e-9) -> dict:
    """Compare the parsed answer against expected value(s); returns a dict with
    correct (bool), detail, and the matched/expected canonical numbers. For a
    list, every expected value must match (set compare) with no spurious extra."""
    if ans is None:
        return {"correct": False, "detail": "no ANSWER line parsed",
                "expected": exp.labels if exp.names_only
                            else [v for v, _ in _expected_canon(exp)], "got": None}

    ans = _normalize_scalar_answer(ans)

    # Membership mode: compare the SET of CLASS IRIs. Answer items MUST be IRIs
    # (full IRI or prefix:Local) — a bare string like "aluminium" is rejected, so
    # the model has to return the class it resolved from the data, not free text.
    if exp.names_only:
        want = {_iri_key(x) for x in exp.labels}
        got_raw = _answer_names(ans)
        non_iri = [g for g in got_raw if not _is_iri(g)]
        if non_iri:
            return {"correct": False,
                    "detail": "answer items must be class IRIs, not strings — "
                              f"got bare value(s): {sorted(set(non_iri))[:8]}",
                    "expected": exp.labels, "got": got_raw}
        got = {_iri_key(g) for g in got_raw}
        missing = {x for x in exp.labels if _iri_key(x) not in got}
        extra = {g for g in got_raw if _iri_key(g) not in want}
        ok = not missing and not extra
        detail = "ok" if ok else (
            (f"missing {sorted(missing)} " if missing else "")
            + (f"extra {sorted(extra)}" if extra else "")).strip()
        return {"correct": ok, "detail": detail,
                "expected": exp.labels, "got": got_raw}
    exp_c = _expected_canon(exp)
    got_c, _ = _answer_canon(ans)
    if not got_c:
        return {"correct": False, "detail": "answer had no numeric value",
                "expected": [v for v, _ in exp_c], "got": ans}

    def _round_sig(x: float, sig: int) -> float:
        if x == 0:
            return 0.0
        from math import floor, log10
        return round(x, -int(floor(log10(abs(x)))) + (sig - 1))

    def _sig_figs(x: float) -> int:
        """Significant figures actually carried by a value as written (e.g.
        113.05 -> 5, 49.73 -> 4, 11.8 -> 3, 3.5 -> 2). Used to bound how coarsely
        we may round when comparing — we must never loosen the match BELOW the
        precision the expected golden was stated to."""
        if x == 0:
            return 1
        s = repr(float(x)).lstrip("-")
        if "e" in s or "E" in s:        # scientific: count mantissa digits
            s = s.split("e")[0].split("E")[0]
        digits = s.replace(".", "").lstrip("0")
        return max(1, len(digits.rstrip("0")) or 1)

    def close(a, b):
        # `b` is the EXPECTED golden; `a` is the model's answer.
        # Ordinary method-level agreement (2% relative).
        if math.isclose(a, b, rel_tol=rtol, abs_tol=atol):
            return True
        # Accept a sig-fig rounding match only AT the precision the GOLDEN was
        # stated to (never coarser) — so a low-precision golden (e.g. 3.5, 2 s.f.)
        # still forgives a rounding difference, but a precise golden (e.g. 113.05,
        # 5 s.f.) is held to the 2% rtol: a 4.7%-off answer is NOT waved through by
        # a coarse 2-sig-fig collision (107.76 and 113.05 both round to 110).
        sig = _sig_figs(b)
        if _round_sig(a, sig) == _round_sig(b, sig):
            return True
        # For TINY values (under ~0.01), also accept a small absolute floor.
        return math.isclose(a, b, rel_tol=rtol, abs_tol=0.01)

    def _unc_close(a, b):
        """Uncertainty agreement — a WIDER band than the value tolerance (expected ±
        is the SI's ~2-sig-fig figure vs the model's data-exact value). Accept within
        UNC_RTOL relative, or a small absolute floor for tiny ± values."""
        UNC_RTOL = 0.20          # 20% — covers SI rounding without accepting a
        UNC_ATOL = 0.05          # genuinely wrong propagation
        return math.isclose(float(a), float(b), rel_tol=UNC_RTOL, abs_tol=UNC_ATOL)

    def _uncertainty_gate(labelled: bool):
        """When the case is a `__valunc` variant (exp.score_uncertainty), the answer
        must ALSO carry uncertainties that match the SI ± within a (wider) tolerance.
        Returns (ok, detail_suffix). For a value-only case returns (True, "")."""
        if not exp.score_uncertainty:
            return True, ""
        if not exp.uncertainties:
            return True, ""        # nothing to score against (shouldn't happen)
        got_u = ans.get("uncertainties") or ans.get("uncertainty")
        if got_u is None:
            return False, " no uncertainties in answer (question asked for them)"
        if isinstance(got_u, dict):
            got_list = [got_u.get(lab) for lab in exp.labels] if labelled else \
                       list(got_u.values())
        elif isinstance(got_u, (list, tuple)) and labelled:
            # Answer may carry MORE rows than expected (e.g. one row per recycling
            # route per element). Align by label rather than position: build a
            # label→unc map (first occurrence wins, same policy as got_pairs for
            # values) and then look up each expected label.
            ans_labels = [str(x) for x in (ans.get("labels") or [])]
            if ans_labels:
                unc_by_label = {}
                for lab, u in zip(ans_labels, got_u):
                    k = _iri_key(lab)
                    if k not in unc_by_label:
                        unc_by_label[k] = u
                got_list = [unc_by_label.get(_iri_key(lab)) for lab in exp.labels]
            else:
                got_list = list(got_u)
        elif isinstance(got_u, (list, tuple)):
            got_list = list(got_u)
        else:
            got_list = [got_u]
        bad = []
        for i, eu in enumerate(exp.uncertainties):
            gu = got_list[i] if i < len(got_list) else None
            lab = exp.labels[i] if labelled and i < len(exp.labels) else str(i)
            if gu is None:
                bad.append(f"{lab}: unc missing")
            elif not _unc_close(float(gu), float(eu)):
                bad.append(f"{lab}: unc got {float(gu):.4g} exp {float(eu):.4g}")
        if bad:
            return False, " unc-wrong [" + "; ".join(bad) + "]"
        return True, ""

    # CLASS-IDENTITY scoring for a LABELLED list: labels MUST be class IRIs and
    # each value must attach to the RIGHT class (a string label, or the right
    # number under the wrong/absent class, is wrong). "Classes, not strings".
    if exp.labels and len(exp.labels) == len(exp.values):
        # the labels the answer attached to its values (NOT the numeric values)
        got_labels = [str(x) for x in (ans.get("labels") or [])]
        if not got_labels:
            # labels may instead ride on each value dict
            got_labels = [str(v.get("label") or v.get("iri") or v.get("class") or "")
                          for v in ans.get("values", []) if isinstance(v, dict)]
        non_iri = [g for g in got_labels if g and not _is_iri(g)]
        if non_iri:
            return {"correct": False,
                    "detail": "labels must be class IRIs, not strings — got bare "
                              f"value(s): {sorted(set(non_iri))[:8]}",
                    "expected": exp.labels, "got": got_labels}
        # pair each answer label with its value (positional, as emitted)
        got_vals = ans.get("values", [])
        got_pairs = {}
        for i, lab in enumerate(got_labels):
            if i < len(got_vals):
                v = got_vals[i]
                n = _num_or_none(v.get("value", v.get("amount"))
                                 if isinstance(v, dict) else v)
                if n is not None:
                    got_pairs[_iri_key(lab)] = canonical(n, str(ans.get("unit", "")))
        missing, mismatched = [], []
        for lab, ev in zip(exp.labels, exp.values):
            k = _iri_key(lab)
            ec = canonical(ev, exp.unit)
            if k not in got_pairs:
                missing.append(lab)
            elif not close(got_pairs[k][0], ec[0]):
                mismatched.append(f"{k}: got {got_pairs[k][0]:.4g} exp {ec[0]:.4g}")
        want_keys = {_iri_key(x) for x in exp.labels}
        # The unattributed-remainder constituent (unknown*) is a REAL data row the SI
        # tables don't tabulate, so a model reporting it is not WRONG — only named
        # constituents are scored, and an unknown* row is never a spurious extra.
        extra = [lab for lab in got_labels
                 if _iri_key(lab) not in want_keys
                 and not _iri_key(lab).lower().startswith("unknown")]
        unc_ok, unc_detail = _uncertainty_gate(labelled=True)
        # ROUTE gate: when exp.routes is set, the answer must carry a "routes" map
        # {constituent IRI: process IRI} with each expected process matching.
        route_ok, route_detail = True, ""
        if exp.routes:
            _raw_routes = ans.get("routes") or {}
            # answer may use the list-of-triples shape [{element,base_metal,route}]
            # instead of the dict shape {elementIRI: processIRI} — normalise to dict
            if isinstance(_raw_routes, list):
                got_routes = {}
                for row in _raw_routes:
                    if isinstance(row, dict):
                        el = str(row.get("element", ""))
                        rt = str(row.get("route", ""))
                        if el and rt and el not in got_routes:
                            got_routes[el] = rt
            else:
                got_routes = _raw_routes if isinstance(_raw_routes, dict) else {}
            bad_routes = []
            for lab, exp_proc in exp.routes.items():
                got_proc = got_routes.get(lab) or got_routes.get(_iri_key(lab))
                # also try matching by local-name key
                if got_proc is None:
                    for k, v in got_routes.items():
                        if _iri_key(k) == _iri_key(lab):
                            got_proc = v
                            break
                if got_proc is None:
                    bad_routes.append(f"{_iri_key(lab)}: route missing")
                elif _iri_key(str(got_proc)) != _iri_key(exp_proc):
                    bad_routes.append(f"{_iri_key(lab)}: got {_iri_key(str(got_proc))} exp {_iri_key(exp_proc)}")
            if bad_routes:
                route_ok = False
                route_detail = " route-wrong [" + "; ".join(bad_routes) + "]"
        # ROUTE-ROWS gate: when exp.route_rows is set, the answer must carry a "routes"
        # list of {element, base_metal, route} dicts covering every expected triple.
        if exp.route_rows:
            got_triples = set()
            for row in (ans.get("routes") or []):
                if isinstance(row, dict):
                    got_triples.add((
                        _iri_key(str(row.get("element", ""))),
                        _iri_key(str(row.get("base_metal", ""))),
                        _iri_key(str(row.get("route", ""))),
                    ))
            bad_rows = []
            for elem, bm, rt in exp.route_rows:
                if (_iri_key(elem), _iri_key(bm), _iri_key(rt)) not in got_triples:
                    bad_rows.append(f"({_iri_key(elem)},{_iri_key(bm)},{_iri_key(rt)})")
            if bad_rows:
                route_ok = False
                route_detail += " route-rows-missing [" + "; ".join(bad_rows) + "]"
        # RANKING gate: for a "rank … in decreasing order" case, the answer's values
        # — IN THE ORDER THE MODEL EMITTED THEM — must be non-increasing. Right values
        # in the wrong order is a wrong ranking. (We read the emitted order from the
        # answer's own value sequence, not the per-label dict, so order is preserved.)
        rank_ok, rank_detail = True, ""
        if exp.ranked:
            seq = []
            for v in got_vals:
                n = _num_or_none(v.get("value", v.get("amount"))
                                 if isinstance(v, dict) else v)
                if n is not None:
                    seq.append(n)
            if any(b - a > abs(a) * rtol + atol for a, b in zip(seq, seq[1:])):
                rank_ok = False
                rank_detail = " not-in-decreasing-order"
        ok = not missing and not mismatched and not extra and unc_ok and route_ok and rank_ok
        detail = "ok" if ok else (
            (f"missing {missing} " if missing else "")
            + (f"wrong-value [{'; '.join(mismatched)}] " if mismatched else "")
            + (f"extra {extra}" if extra else "") + unc_detail + route_detail + rank_detail).strip()
        return {"correct": ok, "detail": detail,
                "expected": exp.labels, "got": got_labels}

    # Fallback (no labels on the expected — e.g. a single bare value): match by
    # number only.
    # Greedy match each expected value to an unused got value.
    remaining = list(got_c)
    matched = []
    for ev, edim in exp_c:
        hit = None
        for i, (gv, gdim) in enumerate(remaining):
            # compare in canonical space; if either is 'raw' compare raw numbers
            if close(ev, gv):
                hit = i
                break
        if hit is None:
            return {"correct": False,
                    "detail": f"expected {ev} ({edim}) not matched in answer",
                    "expected": [v for v, _ in exp_c],
                    "got": [v for v, _ in got_c]}
        matched.append(remaining.pop(hit))
    extra = remaining
    value_ok = len(extra) == 0 or len(exp_c) == 1   # single-value Qs tolerate extras
    unc_ok, unc_detail = _uncertainty_gate(labelled=False)
    ok = value_ok and unc_ok
    detail = "ok" if ok else (
        ("" if value_ok else f"{len(extra)} unexpected extra value(s)") + unc_detail
    ).strip()
    return {"correct": ok, "detail": detail,
            "expected": [v for v, _ in exp_c],
            "got": [v for v, _ in got_c]}


# --- Error category --------------------------------------------------------- #
# Triage every failure into ONE stable category, derived from the score `detail`
# (and the grounding/shape rejections raised in the agent loop), so the bench
# output and observer SHOW what KIND of error each fail is — not just that it
# failed. The categories mirror the manual triage: a resolution (wrong class/whole)
# error, an arithmetic (wrong value) error, an uncertainty error, a no-answer /
# grounding / shape error. Returns "" for a correct case.
ERROR_CATEGORIES = (
    "wrong-class",      # resolved the question to the wrong class/subject to query
    "wrong-value",      # right class, wrong number — aggregation/arithmetic
    "wrong-uncertainty",# value ok, ± off
    "wrong-route",      # value+uncertainty ok, recovery process IRI off
    "no-answer",        # empty / unparseable / not-matched
    "not-grounded",     # answered without a SPARQL tool call
    "wrong-shape",      # answer shape can't be scored (scalar value on labelled case)
    "provider-error",   # the LLM provider failed the call (rate limit / 5xx / auth)
    "timeout",          # wall-clock deadline exceeded before an answer was produced
    "token-cap",        # token budget exhausted before an answer was produced
)


def classify_error(correct, detail: str | None, *,
                   timed_out: bool = False, token_capped: bool = False) -> str:
    """Map a (correct, detail) pair to ONE error category (see ERROR_CATEGORIES).
    Pure string classification over the detail vocabulary score_answer / the agent
    loop produce — no data access, no leak. '' when correct.

    timed_out / token_capped are passed through from run_one so limit failures get
    their own distinct category instead of collapsing into "no-answer"."""
    if correct:
        return ""
    # ASK MODE: correct is None (not False) — there was no expected answer to score
    # against, so this is neither a pass nor a failure. No error category.
    if correct is None:
        return ""
    # Infrastructure/budget limits — check BEFORE reasoning categories so a run that
    # hit the deadline and produced no answer is never mislabelled as a model error.
    if token_capped:
        return "token-cap"
    if timed_out:
        return "timeout"
    d = (detail or "").lower()
    # a provider/transport failure (rate limit, 5xx, auth) is NOT a model mistake —
    # surface it distinctly so it is never mistaken for a reasoning/grounding fail.
    if ("chat failed" in d or "rate limit" in d or "ratelimit" in d
            or "provider error" in d):
        return "provider-error"
    if "not grounded" in d:
        return "not-grounded"
    if "wrong shape" in d or "shape" in d and "reject" in d:
        return "wrong-shape"
    # resolution errors: the class set is wrong (missing/extra/bare-string labels).
    # A pure wrong-VALUE (right class) is arithmetic; mixed counts as wrong-class
    # because the class set must be fixed first.
    if ("missing " in d or "extra " in d or "must be class iris" in d):
        return "wrong-class"
    if "wrong-value" in d or "not matched in answer" in d:
        return "wrong-value"
    if "unc-wrong" in d or ("uncertaint" in d and "no " in d):
        return "wrong-uncertainty"
    if "route-wrong" in d or "route-rows-missing" in d:
        return "wrong-route"
    if ("no numeric value" in d or "no answer" in d or "no answer line" in d
            or "had no" in d):
        return "no-answer"
    return "other"

from __future__ import annotations

import json
import sys as _sys

from .scoring import _answer_label_items, _answer_numbers, _iri_key


def _temperature() -> float:
    import os
    try:
        return float(os.getenv("BENCH_TEMPERATURE", "0.0"))
    except (TypeError, ValueError):
        return 0.0


def _load_helper(name):
    import importlib
    try:
        return importlib.import_module(f"helpers.{name}")
    except Exception as e:  # noqa: BLE001
        print(f"[bench] WARNING: helper {name!r} disabled — import failed: {e!r}. "
              f"Guards/feedback for this helper are OFF. (Is rdflib installed?)",
              file=_sys.stderr, flush=True)
        return None


helper_autoprefix = _load_helper("autoprefix")
helper_classcheck = _load_helper("classcheck")
helper_predicatecheck = _load_helper("predicatecheck")
helper_iriguard = _load_helper("iriguard")
helper_valuesguard = _load_helper("valuesguard")
helper_emptydiagnoser = _load_helper("emptydiagnoser")
helper_feedback = _load_helper("feedback")
if all(h is None for h in (helper_autoprefix, helper_classcheck,
                           helper_predicatecheck, helper_iriguard, helper_valuesguard,
                           helper_emptydiagnoser, helper_feedback)):
    print("[bench] WARNING: ALL helpers are disabled — running with NO guards, "
          "NO prefix-fix, NO feedback. Check the rdflib dependency.",
          file=_sys.stderr, flush=True)


def all_values_off_by_constant_ratio(ans, exp, tol: float = 0.015) -> bool:
    """True if EVERY matched label's value is off from the golden by the SAME ratio
    (≠ 1) — the fingerprint of a WRONG WHOLE/scope: the constituent fractions are right
    but they were multiplied by the wrong itemMass, so the whole list scales by one
    constant. (A genuine arithmetic slip on a multi-row breakdown is per-row random,
    not a uniform scale.) Used to re-classify such a list as wrong-subject so it gets
    the re-resolve re-prompt instead of being left as plain wrong-value.

    Requires the answer to carry per-label values matching the golden's labels by class
    identity; needs ≥2 matched rows (one row can't establish a pattern)."""
    if not ans or not exp.labels or exp.names_only:
        return False
    if len(exp.labels) < 2 or len(exp.labels) != len(exp.values or []):
        return False
    got_labels = [str(x) for x in (ans.get("labels") or [])]
    got_vals = ans.get("values") or []
    if not got_labels or len(got_labels) != len(got_vals):
        return False
    got = {}
    for lab, v in zip(got_labels, got_vals):
        n = v.get("value", v.get("amount")) if isinstance(v, dict) else v
        try:
            got[_iri_key(lab).lower()] = float(n)
        except (TypeError, ValueError):
            continue
    ratios = []
    for lab, ev in zip(exp.labels, exp.values):
        gv = got.get(_iri_key(lab).lower())
        if gv is None or ev in (0, 0.0) or gv == 0:
            return False
        ratios.append(gv / float(ev))
    if len(ratios) < 2:
        return False
    r0 = ratios[0]
    if abs(r0 - 1.0) <= tol:
        return False
    return all(abs(r / r0 - 1.0) <= tol for r in ratios)


def answer_is_fraction_not_kg(ans, exp) -> bool:
    """True when the answer reported the kg/kg FRACTION instead of absolute kg — the
    `fq:amount` was never multiplied by `fq:itemMass` (and/or it was read inside a
    sub-scope, not the whole). Signature: the expected unit is a mass (kg), the
    answer's unit is a ratio ("kg/kg", "%", a bare fraction), the value(s) look like
    fractions (all < ~1.5) while a kg golden is ≥ 1.5. Covers BOTH a labelled
    breakdown (where the answer's labels match the expected classes — so it's a units
    bug, not a wrong-class bug) AND a scalar TOTAL (no labels — the unit+magnitude
    signature alone is enough; there is no class to be 'wrong' about). NOT a
    wrong-class error — tell the model to multiply by itemMass, not to re-resolve."""
    if not ans or not exp or exp.names_only:
        return False
    if "kg" not in (exp.unit or "").lower():
        return False
    unit = str(ans.get("unit", "")).strip().lower()
    is_ratio_unit = ("kg/kg" in unit or unit in ("", "%", "fraction", "ratio")
                     or unit.endswith("/kg"))
    if not is_ratio_unit:
        return False
    if exp.labels:
        got = {_iri_key(x).lower() for x in _answer_label_items(ans)}
        want = {_iri_key(x).lower() for x in exp.labels}
        if not (got and (want & got)):
            return False
    nums = _answer_numbers(ans)
    return bool(nums) and all(abs(n) < 1.5 for n in nums) \
        and any(abs(v) >= 1.5 for v in (exp.values or []))


def _short(obj, n=160) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def _args_for_log(args) -> str:
    """Render a tool call's args for the step log WITHOUT EVER truncating the SPARQL
    query — a cut query cannot be debugged (you can't see why it was blocked or
    returned nothing). The `sparql_query` is printed in full, verbatim; the other,
    shorter args are abbreviated as before to keep the line readable."""
    if not isinstance(args, dict) or "sparql_query" not in args:
        return _short(args)
    q = args["sparql_query"]
    rest = {k: v for k, v in args.items() if k != "sparql_query"}
    head = _short(rest) + ", " if rest else ""
    return f'{head}"sparql_query": {q!r}'


def _fetched_resolve_class(messages) -> bool:
    """True if the attempt called get_skill("resolve-class") at least once — i.e. the
    model actually pulled the class-resolution METHOD before answering. Used to
    ENFORCE the method (not the answer): a tool call is {name, arguments}."""
    for m in messages or []:
        for c in (m.get("tool_calls") or []):
            if c.get("name") == "get_skill":
                args = c.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if str(args.get("skill_id", "")).strip() == "resolve-class":
                    return True
    return False


def _trace_summary(messages, limit=14) -> str:
    """A compact, PLAIN-TEXT recap of what the run actually did — every SPARQL it
    ran and whether that query came back empty — so the 'why did you struggle'
    turn (and the user) can see WHERE it went wrong. No tool_call structure (which
    would orphan a tool result and break the follow-up call); just readable lines."""
    lines = []
    pending = None
    for m in messages or []:
        role = m.get("role")
        if role == "assistant":
            for c in (m.get("tool_calls") or []):
                nm = c.get("name")
                args = c.get("arguments") or {}
                if isinstance(args, str):
                    try: args = json.loads(args)
                    except Exception: args = {}
                if nm == "execute_sparql_query":
                    pending = (args.get("sparql_query") or "").strip()
                    one = " ".join(pending.split())
                    lines.append(f"- ran SPARQL: {one}")
                elif nm == "get_skill":
                    lines.append(f"- fetched skill: {args.get('skill_id')}")
                elif nm == "search_sparql_docs":
                    lines.append("- searched the docs/classes")
        elif role == "tool":
            txt = (m.get("content") or "")
            low = txt.lower()
            if pending is not None:
                empty = ("no results" in low or '"bindings": []' in txt
                         or '"bindings":[]' in txt)
                lines[-1] += "  -> EMPTY (0 rows)" if empty else "  -> got rows"
                pending = None
    return "\n".join(lines[-limit:]) if lines else "(no tool calls were made)"


async def _ask_why_struggled(llm_client, messages, tc, detail) -> str:
    """Force the model to explain, in its own words, WHY it failed and WHERE — and
    GUARANTEE a non-empty result. Uses a CLEAN context (system + the question + a
    plain-text recap of what it tried, with empties marked), so no orphaned
    tool-call can make the follow-up call error out. Retries; if the model still
    returns nothing, returns a trace-derived explanation rather than blank.

    The system + user prompts are recovered from the attempt's own captured
    `messages` (its first system / first user message) — run_one does not keep
    them as locals (they are built inside each attempt), so deriving them here
    avoids a scope error and keeps the recap faithful to what the model saw."""
    system = next((m.get("content", "") for m in messages
                   if m.get("role") == "system"), "")
    user = next((m.get("content", "") for m in messages
                 if m.get("role") == "user"), tc.question)
    recap = _trace_summary(messages)
    ask = (
        "Your answer to the question above was graded INCORRECT (either you produced "
        "no usable answer, or you produced a WRONG value). Here is a recap of what "
        "you tried (queries that returned 0 rows are marked EMPTY):\n\n"
        f"{recap}\n\n"
        f"(grader note — why it was marked wrong: {detail or 'no usable answer'})\n\n"
        "In 2-4 sentences, plainly explain WHY your answer was wrong and WHERE it "
        "went wrong: which class/term you could not resolve or resolved wrongly, "
        "which relationship or predicate you failed to traverse, which query "
        "returned nothing (and why), or which step you skipped — and what you would "
        "do differently. Be concrete and honest — this is diagnostic feedback, NOT "
        "another answer. Do NOT output an ANSWER line or any SPARQL.")
    ctx = [{"role": "system", "content": system},
           {"role": "user", "content": user},
           {"role": "user", "content": ask}]
    for _try in range(2):
        try:
            resp = await llm_client.chat(ctx, [], temperature=_temperature())
            txt = ((resp.get("message") or {}).get("content") or "").strip()
            if txt:
                return txt
        except Exception:  # noqa: BLE001
            pass
    return ("The model did not return an explanation. From its trace, here is what "
            f"it tried:\n{recap}")


async def mcp_tools_to_ollama(session) -> list:
    """Convert the MCP server's tool list into Ollama function-tool schemas."""
    listed = await session.list_tools()
    tools = []
    for t in listed.tools:
        tools.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": (t.description or "")[:1024],
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            },
        })
    return tools

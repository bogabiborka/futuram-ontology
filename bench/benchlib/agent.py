"""The Ollama + MCP agent loop: run_one drives re-prompting attempts, _attempt
runs one tool loop. Contains LLM-FACING re-prompt notes (leak-checked). The
VoID pre-exec helpers (bench/helpers/) load here; a failed import WARNs loudly."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys as _sys
import time
from pathlib import Path

from .cases import TestCase
from .helpers import (
    _args_for_log,
    _ask_why_struggled,
    _fetched_resolve_class,
    _load_helper,
    _short,
    _temperature,
    _trace_summary,
    all_values_off_by_constant_ratio,
    answer_is_fraction_not_kg,
    helper_autoprefix,
    helper_classcheck,
    helper_emptydiagnoser,
    helper_feedback,
    helper_iriguard,
    helper_predicatecheck,
    helper_valuesguard,
    mcp_tools_to_ollama,
)
from .prompts import BACKEND_HINTS, BACKEND_MAIN_SKILL, SYSTEM_TMPL
from .reprompt import build_reprompt
from .scoring import (
    _answer_label_items, _answer_numbers, _expected_canon, _iri_key, _is_iri,
    answer_has_string_labels, answer_missing_labels, answer_missing_uncertainties,
    answer_shape_mismatch, classify_error, parse_answer, score_answer,
    ungrounded_answer_numbers,
)

# Appended to corrective re-prompts: every question HAS a complete answer in this
# endpoint, so a rejection means "fix the query", never "the data is missing". Keeps
# the model from surrendering after a reject. Leak-safe: names no class/value/axis.
_IN_DATA_REASSURE = (
    " (The correct answer IS in this dataset — this rejection means your query/answer "
    "needs fixing, NOT that the data is missing. Keep going until you have it.)")


async def run_one(session, llm_client, model: str, tools_schema: list,
                  tc: TestCase, backend_id: str, endpoint_url: str,
                  max_steps: int, verbose: bool,
                  deadline_seconds: float = 600.0,
                  token_budget: int | None = None,
                  skill_text: str | None = None,
                  max_attempts: int = 1,
                  live_path: "Path | None" = None,
                  run_meta: "dict | None" = None,
                  accept_first: bool = False) -> dict:
    """Run the Ollama tool loop for one (question, backend); returns a result dict
    (per-tool timing, steps, final text, parsed answer). An answer is accepted only
    if grounded in execute_sparql_query, within the per-question wall-clock deadline.

    accept_first: ASK MODE — there is no expected answer, so nothing to score the
    answer against and no reason to re-prompt toward a "correct" value. The FIRST
    grounded, parseable answer the model gives is taken as-is; all corrective
    re-prompt gates (resolve-class method, answer shape, IRI labels, uncertainties,
    grounded-numbers) are skipped."""
    deadline = time.monotonic() + deadline_seconds
    t_run0 = time.monotonic()
    # The ground truth to score THIS backend against: a per-backend override if the
    # case declares one (e.g. fq's year-scoped scope-node IRI vs composition's
    # component-class IRI), else the shared default. Bound once; used everywhere below.
    expected = tc.expected_for(backend_id)
    tool_timings: list[dict] = []          # one entry per tool call, with seconds
    attempts: list[dict] = []
    attempt_no = 0
    reprompt_note = ""
    label_retries = 0      # fair retries when the answer used string (non-IRI) labels
    missing_label_retries = 0  # fair retries when right number(s) but NO class IRI label
    recompute_retries = 0  # fair retries when answer numbers weren't in any result
    unc_retries = 0        # fair retries when a __valunc question got NO uncertainties
    shape_retries = 0      # fair retries when answer shape can't be scored (scalar value on a labelled case)
    frac_retries = 0       # fair retries when the answer is a kg/kg FRACTION but kg expected (classes right; multiply by itemMass)
    resolve_retries = 0    # fair retries when the model answered WITHOUT running the resolve-class method first
    subject_retries = 0    # fair retries when the answer's CLASS SET is wrong (likely the wrong subject) — up to 5
    order_retries = 0      # fair retries when a RANKED case has the right values but the WRONG order
    repeat_retries = 0     # fair retries when the model RESUBMITS an answer already rejected
    rejected_signatures: set = set()   # signatures of WRONG answers already handed in
    chosen = None          # the accepted/failed attempt; set in the loop or fallback below
    provider_error: str | None = None  # set if the LLM provider failed the call

    all_transcripts: list[dict] = []       # every attempt's full message history

    # The ground-truth answer is known UP FRONT (it's in the testcase) — include it
    # in every live write so the observer shows EXPECTED while the case is still
    # running, not only once it finishes.
    if expected.names_only:
        _expected_payload = {"names": expected.labels, "unit": expected.unit}
    else:
        _expected_payload = {"values": expected.values,
                             "unit": expected.unit,
                             "labels": expected.labels}

    def _write_live(cur_attempt_no, cur_messages, t_in, t_out):
        """Write the in-progress transcript to live_path (per tool-call / per
        attempt). Carries question + expected so the UI is fully populated live."""
        if live_path is None:
            return
        # finished attempts + the in-progress one (if messages given)
        convo = list(all_transcripts)
        if cur_messages is not None:
            convo = convo + [{"attempt": cur_attempt_no, "messages": cur_messages,
                              "tokens_in": t_in, "tokens_out": t_out}]
        spent_now = sum(t.get("tokens_in", 0) + t.get("tokens_out", 0)
                        for t in convo)
        try:
            live_path.write_text(json.dumps({
                "case_id": tc.id, "backend": backend_id, "status": "running",
                "question": tc.question, "expected": _expected_payload,
                # run_meta = which model/provider/params produced this run, so the
                # observer shows it WHILE running (not only after completion).
                "run_meta": run_meta or {},
                "attempts_so_far": cur_attempt_no,
                "tokens_so_far": spent_now,
                "conversation": convo,
            }, indent=2, default=str))
        except Exception:
            pass

    while time.monotonic() < deadline and attempt_no < max_attempts:
        spent = (sum(a.get("tokens_in", 0) + a.get("tokens_out", 0)
                     for a in attempts))
        if token_budget is not None and spent >= token_budget:
            break
        attempt_no += 1
        a = await _attempt(
            session, llm_client, model, tools_schema, tc, backend_id,
            endpoint_url, max_steps, verbose, deadline, tool_timings,
            reprompt_note, attempt_no, skill_text,
            token_budget=token_budget, tokens_spent_before=spent,
            on_step=_write_live)              # live flush after EACH tool-call
        attempts.append(a)
        all_transcripts.append({"attempt": attempt_no,
                                "messages": a.get("messages", []),
                                "tokens_in": a.get("tokens_in", 0),
                                "tokens_out": a.get("tokens_out", 0)})
        # also flush at attempt boundary (captures the final, no-tool-call turn)
        _write_live(attempt_no, None, 0, 0)
        # PROVIDER FAILURE short-circuit: a chat-layer error (rate limit / 5xx /
        # auth) that survived the adapter's own backoff is NOT a model mistake and
        # re-prompting only hits the same wall — and would burn the whole attempt
        # budget in seconds, mislabeled as "not grounded". Abort the case now and
        # let it score as a distinct provider-error (see classify_error).
        _err = (a.get("error") or "")
        if ("chat failed" in _err.lower() and not a.get("used_sparql")
                and a.get("answer") is None):
            provider_error = _err
            chosen = a           # this attempt is the chosen (failed) one
            break
        # Accept only a tool-grounded, parseable answer. A CORRECTIVE re-prompt
        # must NOT spend the max_attempts budget (each branch decrements attempt_no
        # before continue); only a genuine fail counts against max_attempts.
        if a["answer"] is not None and a["used_sparql"]:
            # ASK MODE: no expected answer -> nothing to score against, no reason to
            # re-prompt toward "correct". Take the FIRST grounded answer as-is.
            if accept_first:
                chosen = a
                break

            result = build_reprompt(
                attempt=a,
                expected=expected,
                backend_id=backend_id,
                skill_text=skill_text,
                verbose=verbose,
                frac_retries=frac_retries,
                resolve_retries=resolve_retries,
                shape_retries=shape_retries,
                label_retries=label_retries,
                missing_label_retries=missing_label_retries,
                unc_retries=unc_retries,
                recompute_retries=recompute_retries,
                order_retries=order_retries,
                subject_retries=subject_retries,
                repeat_retries=repeat_retries,
                rejected_signatures=rejected_signatures,
            )
            # Update all retry counters from the result
            frac_retries = result["frac_retries"]
            resolve_retries = result["resolve_retries"]
            shape_retries = result["shape_retries"]
            label_retries = result["label_retries"]
            missing_label_retries = result["missing_label_retries"]
            unc_retries = result["unc_retries"]
            recompute_retries = result["recompute_retries"]
            order_retries = result["order_retries"]
            subject_retries = result["subject_retries"]
            repeat_retries = result["repeat_retries"]
            rejected_signatures = result["rejected_signatures"]

            if result["accept"]:
                chosen = a
                break

            if result["decrement_attempt"]:
                attempt_no -= 1
            reprompt_note = result["reprompt_note"]
            continue

        # Token cap or wall-clock timeout: stop, don't re-prompt.
        if a.get("token_capped") or a["timed_out"]:
            chosen = a
            break
        if a["answer"] is None:
            reprompt_note = ("Your previous reply had no valid `ANSWER:` line. "
                             "You MUST run execute_sparql_query and end with the "
                             "ANSWER line in the exact required format.")
        elif not a["used_sparql"]:
            reprompt_note = ("You answered WITHOUT running any SPARQL query. That "
                             "is not allowed: you MUST obtain the number(s) by "
                             "calling execute_sparql_query against the endpoint, "
                             "then give the ANSWER line.")
        if verbose:
            print(f"      [{backend_id}] re-prompting (attempt {attempt_no}): "
                  f"{reprompt_note[:60]}…", flush=True)

    # If the loop ended WITHOUT accepting an attempt (max_attempts reached, or a
    # break on an exhausted token-budget / deadline before any attempt was chosen),
    # fall back to the last attempt — or a synthetic empty one if none ran. This is
    # an explicit post-loop guard rather than a `while…else`, because a `break`
    # skips the `else` clause and would leave `chosen` unbound.
    if chosen is None:
        chosen = attempts[-1] if attempts else {
            "answer": None, "used_sparql": False, "final_text": "",
            "error": "no attempt completed", "timed_out": True,
            "messages": [], "tokens_in": 0, "tokens_out": 0, "token_capped": False}

    elapsed = round(time.monotonic() - t_run0, 2)
    answer = chosen["answer"]
    used_sparql = chosen["used_sparql"]
    # A provider failure (rate limit / 5xx / auth, after the adapter's own retries)
    # is scored as its own category — NOT a model grounding/reasoning mistake.
    if provider_error:
        score = {"correct": False,
                 "detail": f"chat failed: {provider_error}",
                 "expected": [v for v, _ in _expected_canon(expected)],
                 "got": None}
    # ASK MODE: there is no expected answer, so there is NOTHING to score against.
    # Record the grounded answer as-is (correct=None) and skip both scoring AND the
    # struggle introspection below — otherwise score_answer() against an empty
    # `expected` returns correct=False, which would (a) mislabel the run a failure
    # and (b) trigger the "why did you fail?" turn whose output the observer renders
    # as "the model's note". A non-grounded ask answer is still rejected (next branch).
    elif accept_first and used_sparql:
        # `answer` is already the PARSED answer (chosen["answer"]), not raw text —
        # surface it as-is; do NOT re-run parse_answer (it expects a string).
        score = {"correct": None,
                 "detail": "ask mode: not scored (no expected answer)",
                 "expected": None,
                 "got": answer}
    # Reject (mark incorrect) a non-tool-grounded answer even if the number is right.
    elif not used_sparql:
        score = {"correct": False,
                 "detail": "rejected: answer not grounded in a SPARQL tool call",
                 "expected": [v for v, _ in _expected_canon(expected)],
                 "got": None}
    else:
        score = score_answer(expected, answer)

    n_sparql = sum(1 for t in tool_timings if t["tool"] == "execute_sparql_query")
    # The FINAL SPARQL query — the last execute_sparql_query in the answering
    # attempt (THE query that grounded the result), surfaced on its own field for
    # quick auditing, separate from the full per-call query log.
    final_sparql = None
    for _m in reversed(chosen.get("messages", []) or []):
        if _m.get("role") == "assistant":
            for _c in (_m.get("tool_calls") or []):
                if _c.get("name") == "execute_sparql_query":
                    final_sparql = (_c.get("arguments") or {}).get("sparql_query")
                    break
        if final_sparql is not None:
            break

    # STRUGGLE INTROSPECTION — on ANY failed run (a limit/no-answer failure OR a
    # wrong answer), there is no useful "final answering query" to show: the useful
    # thing to surface is WHY it failed, not a query that produced the wrong number.
    # So ask the model, in one extra turn, WHY it failed, and surface THAT in the
    # prominent slot. Runs for every non-correct outcome (token cap, timeout, no
    # parseable/grounded answer, OR a wrong value) — only a CORRECT run keeps its
    # final_sparql for auditing.
    struggle_reason = None
    # Skip the introspection turn on a provider failure — the model can't be asked
    # why it "struggled" when the provider itself rejected every call. Also skip in
    # ask mode entirely (accept_first): there is no expected answer, so there is no
    # failure to explain — and its output would surface as "the model's note".
    if score["correct"] is False and not provider_error and not accept_first:
        struggle_reason = await _ask_why_struggled(
            llm_client, chosen.get("messages", []) or [],
            tc, score.get("detail"))

    by_tool = {}
    for t in tool_timings:
        by_tool.setdefault(t["tool"], {"calls": 0, "seconds": 0.0})
        by_tool[t["tool"]]["calls"] += 1
        by_tool[t["tool"]]["seconds"] = round(
            by_tool[t["tool"]]["seconds"] + t["seconds"], 3)
    # token totals across ALL attempts (the true cost of getting this answer)
    tokens_in = sum(a.get("tokens_in", 0) for a in attempts)
    tokens_out = sum(a.get("tokens_out", 0) for a in attempts)

    # ---- LOAD KPIs: how much effort each backend needed to reach the answer ----
    # Quantify the "required load" so fq vs composition compare on cost, not just
    # correctness; all derived from tool_timings + every attempt's message log.
    all_msgs = [m for a in attempts for m in (a.get("messages") or [])]
    assistant_turns = sum(1 for m in all_msgs if m.get("role") == "assistant")
    # bytes of tool-result text the model had to read (the data it waded through)
    result_chars = sum(len(m.get("content") or "")
                       for m in all_msgs if m.get("role") == "tool")
    # SPARQL the model had to author (chars of every query it ran) — query
    # complexity/volume is a direct proxy for how hard the backend was to query.
    sparql_chars = 0
    sparql_lens = []
    for m in all_msgs:
        if m.get("role") == "assistant":
            for c in (m.get("tool_calls") or []):
                if c.get("name") == "execute_sparql_query":
                    qy = (c.get("arguments") or {}).get("sparql_query") or ""
                    sparql_chars += len(qy)
                    sparql_lens.append(len(qy))
    dup_runs = sum(1 for t in tool_timings if t.get("duplicate"))
    # ungrounded SPARQL run = executed but returned no usable data (empty/error):
    # wasted effort, a direct signal of how hard the backend was to query right.
    ungrounded_runs = sum(1 for t in tool_timings
                          if t["tool"] == "execute_sparql_query"
                          and not t.get("grounded") and not t.get("duplicate"))
    tool_seconds = round(sum(t["seconds"] for t in tool_timings), 3)
    # QUERY LOAD TIMES — endpoint latency per executed SPARQL query (seconds the
    # backend itself took to answer each query). The composition endpoint forces
    # heavy tree-walking joins, so its per-query times are the load signal.
    q_times = [round(t["seconds"], 3) for t in tool_timings
               if t["tool"] == "execute_sparql_query" and not t.get("duplicate")]
    sparql_seconds = round(sum(q_times), 3)
    search_seconds = round(sum(t["seconds"] for t in tool_timings
                               if t["tool"] != "execute_sparql_query"), 3)
    kpis = {
        # effort to reach the answer
        "queries_to_answer": n_sparql,          # SPARQL executions
        "tool_calls_total": len(tool_timings),  # incl. search/schema lookups
        "assistant_turns": assistant_turns,     # LLM reasoning rounds
        "attempts": attempt_no,
        # token / data load
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "result_chars_read": result_chars,      # bytes of results waded through
        "sparql_chars_written": sparql_chars,    # total query text authored
        "avg_sparql_len": round(sparql_chars / len(sparql_lens), 1) if sparql_lens else 0,
        "max_sparql_len": max(sparql_lens) if sparql_lens else 0,
        # wasted effort (signals a hard-to-query backend)
        "ungrounded_queries": ungrounded_runs,   # ran but returned no usable data
        "duplicate_queries": dup_runs,           # re-issued identical query
        # WRONG-QUERY RATIO — fraction of SPARQL executions that didn't advance
        # the answer: ungrounded (ran but empty/error) or duplicate. 0.0 = every
        # query productive; high = the backend was hard to query correctly.
        "wrong_queries": ungrounded_runs + dup_runs,
        "wrong_query_ratio": round((ungrounded_runs + dup_runs) / n_sparql, 3) if n_sparql else 0.0,
        # QUERY LOAD TIMES — per-query endpoint latency + aggregates
        "query_times": q_times,                  # seconds per executed query
        "sparql_seconds": sparql_seconds,        # total endpoint query time
        "avg_query_seconds": round(sparql_seconds / len(q_times), 3) if q_times else 0,
        "max_query_seconds": max(q_times) if q_times else 0,
        "search_seconds": search_seconds,        # time in search/schema lookups
        # wall-clock split
        "wall_seconds": elapsed,
        "tool_seconds": tool_seconds,            # time inside endpoints/tools
        "llm_seconds": round(max(0.0, elapsed - tool_seconds), 3),  # time thinking
    }
    return {
        "backend": backend_id,
        "endpoint": endpoint_url,
        "seconds": elapsed,
        "attempts": attempt_no,
        "tool_calls": len(tool_timings),
        "sparql_runs": n_sparql,
        "used_sparql": used_sparql,
        "timed_out": chosen.get("timed_out", False),
        "token_capped": chosen.get("token_capped", False),
        "duplicate_queries": sum(1 for t in tool_timings if t.get("duplicate")),
        "tokens_in": tokens_in,                 # prompt tokens (all calls)
        "tokens_out": tokens_out,               # generated tokens (all calls)
        "tokens_total": tokens_in + tokens_out,
        "tool_seconds_by_type": by_tool,        # PER-TASK timing breakdown
        "tool_timings": tool_timings,           # every call, in order, timed
        "kpis": kpis,                           # LOAD metrics (effort per backend)
        "final_sparql": final_sparql,          # THE query that grounded the answer
        # on a limit/no-answer FAILURE: the model's own explanation of why it
        # struggled, shown in the observer INSTEAD of the final query.
        "struggle_reason": struggle_reason,
        "answer_raw": answer,
        "correct": score["correct"],
        "score_detail": score["detail"],
        # the TRIAGED error category (wrong-class / wrong-value / wrong-uncertainty /
        # no-answer / not-grounded / wrong-shape; "" when correct) — so the bench
        # output and observer SHOW what KIND of error each fail is.
        "error_category": classify_error(score["correct"], score["detail"]),
        "subject_retries": subject_retries,     # how many wrong-subject re-prompts fired
        "repeat_retries": repeat_retries,       # how many were a RESUBMIT of an already-rejected answer
        # TRUE iff the LLM provider itself failed every call (rate-limit / 5xx /
        # auth / NETWORK DROP after the adapter's own backoff) — an INFRASTRUCTURE
        # failure, not a model mistake. run_bench treats this like a raised transient
        # error: wait for the system to recover, then RETRY the whole case.
        "provider_error": bool(provider_error),
        "expected": score["expected"],
        "got": score["got"],
        "final_text": chosen["final_text"].strip(),
        "error": chosen.get("error"),
        # full conversation for EVERY attempt (system/user/assistant/tool turns,
        # tool args + complete tool result text). Stored to the transcript zip,
        # kept OUT of the light results JSON. Stripped before that JSON is written.
        "transcript": all_transcripts,
    }


async def _attempt(session, llm_client, model, tools_schema, tc, backend_id,
                   endpoint_url, max_steps, verbose, deadline, tool_timings,
                   reprompt_note, attempt_no, skill_text=None,
                   token_budget=None, tokens_spent_before=0, on_step=None) -> dict:
    """One independent tool-loop attempt; appends per-call timings to tool_timings.
    token_budget is the TOTAL ceiling for the (question,backend); reaching it stops
    the loop (token_capped). Returns {answer, used_sparql, final_text, error, ...}."""
    from mcp.types import TextContent

    budget_hint = ""
    if token_budget is not None:
        remaining_budget = max(0, token_budget - tokens_spent_before)
        budget_hint = (f"TOKEN BUDGET: about {remaining_budget:,} tokens remain for "
                       f"this question. ")
    system = SYSTEM_TMPL.format(endpoint_url=endpoint_url, backend_id=backend_id,
                                backend_hint=BACKEND_HINTS.get(backend_id, ""),
                                main_skill=BACKEND_MAIN_SKILL.get(backend_id, backend_id),
                                budget_hint=budget_hint)
    if skill_text:
        # skill_text is the nudge telling the model that callable SKILLS exist
        # (list_skills / get_skill tools) — and optionally a lead skill to read
        # first. It is METHOD guidance, never an answer.
        system = f"{system}\n\n{skill_text}"
    # prompt_question = SI-verbatim question + the auto-appended uncertainty ask
    # (only when this case scores uncertainty). The ask is never baked into the YAML.
    user = tc.prompt_question
    if reprompt_note:
        user = f"{tc.prompt_question}\n\n[Retry — previous attempt failed] {reprompt_note}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    # convo is a JSON-serialisable, fully-captured transcript of THIS attempt:
    # every system/user/assistant/tool message, in order, with tool args + the
    # raw tool result text. This is what gets stored per test.
    convo: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    used_sparql = False
    last_error = None
    final_text = ""
    # token accounting for THIS attempt — Ollama returns prompt_eval_count (input
    # tokens, incl. growing context + tool results) and eval_count (generated
    # tokens) per chat call; we sum them across the loop.
    tok_in = 0
    tok_out = 0

    token_capped = False
    # CONTINUOUS COMMIT NUDGING: instead of one warning, escalate as the model keeps
    # querying past sensible limits (the dribble failure runs 100-250 queries). We fire
    # at rising token thresholds AND on raw query count, each more forceful.
    nudge_level = 0          # how many nudges already fired (escalates the wording)
    query_count = 0          # execute_sparql_query calls made in THIS attempt
    saw_numeric_result = False  # a query has returned an actual NUMBER (a value the
                                # answer can be built from), not just IRIs/labels from a
                                # discovery dump. The commit-nudge MUST NOT say "answer
                                # from the rows you have" when this is False — the model
                                # has only found classes, no values yet, and ordering it
                                # to stop forces an empty answer.
    # token thresholds (fraction of budget) at which to fire successive nudges
    _NUDGE_FRACTIONS = (0.5, 0.7, 0.85, 0.95)
    # query-count thresholds (fire once data exists and the model keeps dribbling)
    _NUDGE_QUERY_COUNTS = (8, 14, 20, 28)
    # Cache of SPARQL run in THIS attempt -> prior result, so an identical
    # re-issue replays instead of re-executing. Maps normalised-query ->
    # {"count": n, "result": <prior tool text>}.
    seen_queries: dict[str, dict] = {}

    def _norm_sparql(q: str) -> str:
        # collapse whitespace + case so trivially-reformatted repeats still match
        return re.sub(r"\s+", " ", str(q)).strip().lower()

    def _ret(answer, timed_out, error):
        return {"answer": answer, "used_sparql": used_sparql,
                "final_text": final_text, "error": error,
                "timed_out": timed_out, "token_capped": token_capped,
                "messages": convo,
                "tokens_in": tok_in, "tokens_out": tok_out}

    def _flush():
        # push the in-progress convo to the live observer after EACH turn, so the
        # UI updates per tool-call, not only when the whole attempt finishes.
        if on_step is not None:
            try:
                on_step(attempt_no, list(convo), tok_in, tok_out)
            except Exception:
                pass

    for step in range(max_steps):
        if time.monotonic() >= deadline:
            return _ret(None, True, "timeout")
        # Token cap: stop before the next model call if the running total (this
        # attempt + earlier attempts) has reached the budget.
        if token_budget is not None and \
                tokens_spent_before + tok_in + tok_out >= token_budget:
            token_capped = True
            return _ret(None, True, "token budget exhausted")
        remaining = deadline - time.monotonic()
        # CONTINUOUS COMMIT NUDGE: the model tends to keep querying and never write the
        # ANSWER even once it has the data. Fire REPEATEDLY and ESCALATING as it crosses
        # successive limits — a token-budget fraction, a raw query-count, or the last
        # few steps — so a model that ignores one nudge gets a firmer one. Only nudge
        # once it HAS grounded data (used_sparql); before that it still needs to query.
        spent_total = tokens_spent_before + tok_in + tok_out
        frac = (spent_total / token_budget) if token_budget else 0.0
        # how many thresholds have we crossed on EITHER axis?
        crossed = sum(1 for f in _NUDGE_FRACTIONS if frac >= f)
        crossed = max(crossed, sum(1 for c in _NUDGE_QUERY_COUNTS if query_count >= c))
        near_step_cap = step >= max(1, max_steps - 3)
        want_level = crossed + (1 if near_step_cap else 0)
        if used_sparql and want_level > nudge_level:
            nudge_level = want_level
            if token_budget is not None:
                left = max(0, token_budget - spent_total)
                limit_phrase = (f"You have spent ~{spent_total:,} of your "
                                f"{token_budget:,}-token budget (~{left:,} left) and "
                                f"run {query_count} queries.")
            else:
                limit_phrase = (f"You have run {query_count} queries and are "
                                f"almost out of steps.")
            # escalate the firmness with the nudge level. BUT branch on whether the
            # model actually has NUMBERS yet: if it has only run discovery queries
            # (found classes, no values), ordering it to "answer from the rows you
            # have" forces an EMPTY answer — it must instead run its ONE final value
            # query, THEN answer. Telling a model with no numbers to stop querying
            # forces an empty answer.
            if not saw_numeric_result:
                # No value retrieved yet — do NOT say "stop"; say "one final query".
                force = ("You have NOT yet retrieved any numeric value — you have only "
                         "located the classes. Do NOT stop and do NOT hand in an empty / "
                         "labels-only answer (it scores ZERO). Run ONE final, complete "
                         "query that returns the kg value (itemMass × amount) for every "
                         "class at once — discover the set via the data's grouping "
                         "relation, do not hand-list — and THEN write the ANSWER line "
                         "from those returned numbers. One query, then answer.")
            elif nudge_level >= 3:
                force = ("THIS IS YOUR FINAL WARNING. Output ONLY the ANSWER line "
                         "NOW, computed from the rows you ALREADY have above. Run NO "
                         "further query. If you query again you will be cut off with "
                         "NOTHING and score wrong.")
            elif nudge_level == 2:
                force = ("STOP querying. You are dribbling — running many small "
                         "queries instead of answering. Read the rows you already "
                         "retrieved and write the final ANSWER line immediately.")
            else:
                force = ("You already have query results above. STOP running queries "
                         "now. Read the data you have and write your final ANSWER line "
                         "in the exact required format — a partial answer beats being "
                         "cut off with none. Do not run another query.")
            messages.append({"role": "user", "content": f"{limit_phrase} {force}"})
        try:
            tc0 = time.monotonic()
            resp = await asyncio.wait_for(
                llm_client.chat(messages, tools_schema, temperature=_temperature()),
                timeout=remaining)
            think = round(time.monotonic() - tc0, 3)
        except asyncio.TimeoutError:
            return _ret(None, True, "timeout")
        except Exception as e:  # noqa: BLE001
            return _ret(None, False, f"chat failed: {e}")
        # accumulate token usage reported by Ollama for this call
        step_in = int(resp.get("prompt_eval_count", 0) or 0)
        step_out = int(resp.get("eval_count", 0) or 0)
        tok_in += step_in
        tok_out += step_out
        msg = resp["message"]
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        # Record the assistant turn (content + any tool calls it requested).
        convo.append({
            "role": "assistant",
            "content": msg.get("content", "") or "",
            "thinking_seconds": think,
            "tokens_in": step_in,
            "tokens_out": step_out,
            "tool_calls": [
                {"name": c["function"]["name"],
                 "arguments": c["function"].get("arguments", {})}
                for c in calls],
        })
        _flush()                                   # live update after the LLM turn
        if not calls:
            final_text = msg.get("content", "") or ""
            break
        for call in calls:
            if time.monotonic() >= deadline:
                return _ret(None, True, "timeout")
            fn = call["function"]["name"]
            # tool-call id, present for API providers (OpenAI/Anthropic) so the
            # tool reply can be matched back to its call; Ollama omits it.
            call_id = call.get("_id")
            args = call["function"].get("arguments", {}) or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if fn == "execute_sparql_query":
                args["endpoint_url"] = endpoint_url   # pin to this backend
                query_count += 1                      # for the continuous commit nudge
            if fn == "search_sparql_docs":
                # Force doc retrieval to THIS backend so the other endpoint's
                # vocabulary (e.g. fq:) can't leak in via on-topic cross-endpoint
                # examples. The bench MCP's search filters on endpoint_url.
                args["endpoint_url"] = endpoint_url
            if fn in ("list_skills", "get_skill"):
                # Force skill scoping to THIS backend so the composition model can
                # never list or fetch an fq-vocabulary skill (and vice-versa). The
                # bench MCP filters skills by the backend behind endpoint_url.
                args["endpoint_url"] = endpoint_url
            # VoID-DRIVEN HELPERS (execute_sparql_query only; read VoID, not live
            # data): autoprefix fixes/injects missing PREFIXes; classcheck blocks a
            # query naming a VoID-absent class, returning nearest classes as hints.
            corrected_prefixes: list[str] = []
            injected_prefixes: list[str] = []
            helper_block: str | None = None
            if fn == "execute_sparql_query" and args.get("sparql_query"):
                if helper_autoprefix is not None:
                    try:
                        new_q, chg = helper_autoprefix.apply(
                            args["sparql_query"], endpoint_url)
                        corrected_prefixes = chg.get("corrected", [])
                        injected_prefixes = chg.get("added", [])
                        iri_corrected = chg.get("iri_corrected", [])
                        if corrected_prefixes or injected_prefixes or iri_corrected:
                            args["sparql_query"] = new_q
                    except Exception:  # noqa: BLE001
                        corrected_prefixes = injected_prefixes = []
                if helper_classcheck is not None:
                    try:
                        helper_block = helper_classcheck.check(
                            args["sparql_query"], endpoint_url)
                    except Exception:  # noqa: BLE001
                        helper_block = None
                # predicate guard: catches the other backend's vocabulary
                # (e.g. fq: on the composition endpoint) before it wastes a step.
                if helper_block is None and helper_predicatecheck is not None:
                    try:
                        helper_block = helper_predicatecheck.check(
                            args["sparql_query"], endpoint_url)
                    except Exception:  # noqa: BLE001
                        helper_block = None
                # IRI guard: FORBID string-matching on a class IRI (the IRI is
                # opaque — search rdfs:label / rdfs:comment instead).
                if helper_block is None and helper_iriguard is not None:
                    try:
                        helper_block = helper_iriguard.check(
                            args["sparql_query"], endpoint_url)
                    except Exception:  # noqa: BLE001
                        helper_block = None
                # VALUES guard: FORBID hand-listing a SET (>=3) of class IRIs — the
                # members of a group must be DISCOVERED via rdfs:subClassOf, not typed.
                if helper_block is None and helper_valuesguard is not None:
                    try:
                        helper_block = helper_valuesguard.check(
                            args["sparql_query"], endpoint_url)
                    except Exception:  # noqa: BLE001
                        helper_block = None

            if verbose:
                note = ""
                if corrected_prefixes:
                    note += f" ~fixed-ns[{','.join(corrected_prefixes)}]"
                if injected_prefixes:
                    note += f" +prefix[{','.join(injected_prefixes)}]"
                if helper_block:
                    note += " BLOCKED-bad-class"
                # NEVER truncate the SPARQL query in the step log — a cut query is
                # un-debuggable (you can't tell why it was blocked / 0-rowed). Print
                # the FULL query verbatim; only non-query args are abbreviated.
                print(f"      [{backend_id}] a{attempt_no} step {step} -> "
                      f"{fn}({_args_for_log(args)}){note}", flush=True)

            # CLASS GUARD: a query naming a class the VoID does not describe is not
            # run; we return the hint message in the tool turn so the model fixes
            # the class in its next step instead of staring at an empty result.
            if helper_block is not None:
                content = helper_block
                tool_timings.append({
                    "attempt": attempt_no, "step": step, "tool": fn,
                    "seconds": 0.0, "grounded": False, "duplicate": False,
                    "blocked_bad_class": True, "args": _short(args, 240),
                })
                convo.append({"role": "tool", "name": fn, "args": args,
                              "seconds": 0.0, "grounded": False,
                              "blocked_bad_class": True, "content": content})
                messages.append({"role": "tool", "name": fn, "_id": call_id,
                                 "content": content})
                _flush()
                continue   # skip execution of this bad-class query

            # DUPLICATE SHORT-CIRCUIT: if this exact SPARQL already ran, replay the
            # prior result and tell the model it's unchanged so it must FIX it (or
            # answer) — saves the round-trip and stops budget-burning.
            duplicate = False
            qn = _norm_sparql(args.get("sparql_query", "")) if fn == "execute_sparql_query" else ""
            if qn and qn in seen_queries:
                duplicate = True
                prev = seen_queries[qn]
                prev["count"] += 1
                directive = (
                    "This is the SAME query you already ran — it was NOT executed "
                    "again because the result is identical and will not change. "
                    "It did not get you closer to the answer. Do NOT submit this "
                    "query again. Change the query (fix the predicate/pattern), or "
                    "use the result below to give your ANSWER now.\n"
                    "Its previous result was:\n")
                content = directive + prev["result"]
                ok = False
                secs = 0.0
                tool_timings.append({
                    "attempt": attempt_no, "step": step, "tool": fn,
                    "seconds": secs, "grounded": False, "duplicate": True,
                    "args": _short(args, 240),
                })
                convo.append({"role": "tool", "name": fn, "args": args,
                              "seconds": secs, "grounded": False,
                              "duplicate": True, "content": content})
                messages.append({"role": "tool", "name": fn, "_id": call_id,
                                 "content": content})
                _flush()
                continue   # skip execution of this duplicate call

            ct0 = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    session.call_tool(fn, args),
                    timeout=max(1.0, deadline - time.monotonic()))
                content = "\n".join(
                    c.text for c in result.content
                    if isinstance(c, TextContent)) or "(no text content)"
                ok = True
            except asyncio.TimeoutError:
                # capture the partial tool turn before bailing
                convo.append({"role": "tool", "name": fn, "_id": call_id,
                              "args": args,
                              "content": "(timed out before tool returned)"})
                return _ret(None, True, "timeout")
            except Exception as e:  # noqa: BLE001
                content = f"tool {fn} failed: {e}"
                last_error = content
                ok = False
            secs = round(time.monotonic() - ct0, 3)
            # Tell the model which PREFIX lines the autoprefix helper added, so it
            # adopts the correct namespaces in later queries (it often guesses the
            # futuram namespace wrong).
            if corrected_prefixes or injected_prefixes:
                bits = []
                if corrected_prefixes:
                    bits.append("CORRECTED the namespace of prefix(es) "
                                + ", ".join(corrected_prefixes)
                                + " to the VoID's canonical IRI (your declaration "
                                "used the wrong namespace, which matches nothing)")
                if injected_prefixes:
                    bits.append("added missing PREFIX declaration(s): "
                                + ", ".join(injected_prefixes))
                content = ("[helper] Before running, I " + "; ".join(bits)
                           + ". Use these exact namespaces in future queries.\n"
                           + content)
            # Count a SPARQL call as "grounding" only if it actually returned
            # results (not a validation-error / no-results message).
            grounded = (fn == "execute_sparql_query" and ok
                        and "returned no results" not in content
                        and "not valid according to" not in content
                        and "returned error" not in content)
            if grounded:
                used_sparql = True
                # Did this result carry an actual NUMBER (not just IRIs/labels from a
                # discovery dump)? A JSON results body with a numeric literal is the
                # signal. Gates the commit-nudge so we never order "answer from your
                # rows" when the model has only found classes and has no values yet.
                if re.search(r'"datatype":\s*"[^"]*(?:decimal|double|float|integer|int)"',
                             content) or re.search(
                        r'"type":\s*"literal"[^}]*"value":\s*"-?\d', content):
                    saw_numeric_result = True
            # EMPTY-RESULT DIAGNOSER: a valid query that returned zero rows tells
            # the model nothing. Probe which triple pattern (or FILTER) emptied it
            # and append that hint, so the next step fixes the right thing.
            empty = (fn == "execute_sparql_query" and ok and not grounded
                     and "returned no results" in content)
            had_diagnosis = False
            if empty and helper_emptydiagnoser is not None:
                try:
                    hint = helper_emptydiagnoser.diagnose(
                        args.get("sparql_query", ""), endpoint_url)
                except Exception:  # noqa: BLE001
                    hint = None
                if hint:
                    content = content + "\n" + hint
                    had_diagnosis = True
            # FEEDBACK GUARANTEE: every execute_sparql_query result hands back real
            # data OR an actionable steer — covers the outcomes above didn't
            # (validation reject, error, empty-no-diagnosis); no-op when rows.
            if fn == "execute_sparql_query" and helper_feedback is not None:
                try:
                    content = helper_feedback.augment(
                        content, tool=fn, ok=ok, grounded=grounded,
                        had_diagnosis=had_diagnosis,
                        sparql_query=args.get("sparql_query"))
                except Exception:  # noqa: BLE001
                    pass
            # Remember this query's (final, possibly hint-augmented) result so an
            # identical re-issue replays it.
            if qn:
                seen_queries[qn] = {"count": 1, "result": content}
            tool_timings.append({
                "attempt": attempt_no, "step": step, "tool": fn,
                "seconds": secs, "grounded": grounded, "duplicate": duplicate,
                "args": _short(args, 240),
            })
            # Full tool turn in the transcript (complete result text, not truncated).
            convo.append({
                "role": "tool", "name": fn, "args": args,
                "seconds": secs, "grounded": grounded, "content": content,
            })
            messages.append({"role": "tool", "name": fn, "_id": call_id,
                             "content": content})
            _flush()                               # live update after each query
    else:
        last_error = last_error or f"hit max_steps={max_steps} without final answer"

    answer = parse_answer(final_text)
    return _ret(answer, False, last_error)

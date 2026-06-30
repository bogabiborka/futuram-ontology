# /// script
# requires-python = ">=3.10"
# dependencies = ["ollama", "mcp", "pyyaml", "rdflib", "anthropic", "openai", "google-genai"]
# ///
"""Head-to-head benchmark: the SAME NL question, answered by an Ollama LLM-SPARQL
loop against TWO backends of the same data (query-optimized fq: dataset vs baseline composition), to see
which vocabulary answers better/faster. Backend PINNED per run; CLI entry only.

Run
---
    # 1. bring up the separated bench stack (see the compose file header)
    docker compose -f bench/docker-compose.bench.yml up -d
    # 2. run the benchmark
    uv run bench/run_bench.py bench/testcases/domain.yaml
    # options: --backends fq,composition  --model <tag>  --mcp http://localhost:47898/
    #          --json results.json  --max-steps 8
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

from benchlib.agent import mcp_tools_to_ollama, run_one
from benchlib.prompts import BACKEND_MAIN_SKILL
from benchlib.cases import Expected, TestCase, load_testcases
from benchlib.report import (
    DEFAULT_ENDPOINTS, DEFAULT_LIVE_DIR, DEFAULT_MCP_URL, copilot_quota_snapshot,
    environment, list_models, load_providers, print_summary, provider_models,
    resolve_live_dir, resolve_llm,
)


# Retries of a failed (retryable) case wait on this ESCALATING schedule before each
# attempt: a brief blip recovers fast, then the gap grows. After the schedule is
# exhausted the LAST value REPEATS FOREVER — a retryable failure (network drop, MCP
# transport death, provider outage) is NEVER abandoned; the run just pauses and keeps
# retrying that case until the whole system is healthy again, then resumes. Only a
# deterministic code bug is isolated immediately. Overridable via env (comma list).
_RETRY_DELAYS = [int(x) for x in
                 os.getenv("BENCH_RETRY_DELAYS", "30,120,600,1800").split(",")]
# steady-state interval once the escalating schedule is used up (default 30 min)
_RETRY_FOREVER_EVERY = _RETRY_DELAYS[-1]


class ProviderUnavailable(Exception):
    """The LLM provider failed every call for a case (rate-limit / 5xx / auth / a
    NETWORK DROP that the adapter's own backoff couldn't ride out). run_one returns
    this as a provider_error result rather than raising; we RE-RAISE it as this
    exception so the per-case survivable-retry loop treats it exactly like a
    transport drop — wait for the system to recover, then retry the whole case —
    instead of silently recording one provider-error and moving on. It is NOT in the
    _is_code_bug list, so it is always retried."""


def _is_connection_error(exc: BaseException) -> bool:
    """Heuristic: does this look like the MCP/HTTP transport dropping (e.g. the
    laptop briefly suspended and the streamable-http stream died)? Used to LABEL the
    error record and to decide a reconnect is worth trying."""
    names = {type(e).__name__ for e in (exc, getattr(exc, "__cause__", None),
                                        getattr(exc, "__context__", None)) if e}
    text = f"{type(exc).__name__}: {exc}".lower()
    if names & {"ConnectError", "ConnectionError", "ReadError", "WriteError",
                "RemoteProtocolError", "ClosedResourceError", "EndOfStream",
                "IncompleteRead", "ReadTimeout", "TimeoutException",
                "CancelledError"}:
        return True
    return any(s in text for s in ("connection", "closed", "reset", "broken pipe",
                                   "timed out", "stream", "transport", "cancel"))


def _is_code_bug(exc: BaseException) -> bool:
    """A DETERMINISTIC code bug (NameError, AttributeError, …) — retrying it is
    pointless, so isolate immediately. Everything else (transport drops,
    cancellations, transient system errors) is treated as RETRYABLE."""
    bug_types = (NameError, AttributeError, TypeError, ImportError,
                 IndexError, KeyError, SyntaxError, UnboundLocalError,
                 AssertionError, ValueError)
    chain = [exc, getattr(exc, "__cause__", None), getattr(exc, "__context__", None)]
    return any(isinstance(e, bug_types) for e in chain if e is not None)


# Substrings in a provider error that mean the failure is PERMANENT — it will NEVER
# recover, so retrying forever (the network-blip policy) just hangs the run. A
# retired/decommissioned/removed model, an unknown model id, an invalid/expired key,
# or an unsupported-model rejection are all "switch the model", not "wait it out".
_PERMANENT_PROVIDER_MARKERS = (
    "retired", "decommission", "deprecated", "no longer available", "has been removed",
    "model not found", "unknown model", "does not exist", "invalid model",
    "unsupported model", "model_not_found", "invalid api key", "invalid_api_key",
    "unauthorized", "authentication", "permission denied", "account",
)


def _is_permanent_provider_failure(exc: BaseException) -> bool:
    """True if a provider error names a PERMANENT cause (model retired/unknown, bad
    key/auth). Such a run must be ISOLATED immediately, not retried forever — the
    user has to switch the model/key. Matched on the message text, case-insensitive."""
    text = " ".join(str(e) for e in
                    (exc, getattr(exc, "__cause__", None), getattr(exc, "__context__", None))
                    if e is not None).lower()
    return any(m in text for m in _PERMANENT_PROVIDER_MARKERS)


async def _system_healthy(mcp_url, endpoints) -> bool:
    """Re-check that the system is back before a retry: the MCP HTTP port answers
    AND every backend's SPARQL endpoint answers a trivial ASK. Best-effort — a
    False just means 'not yet', so the caller waits and tries again."""
    import urllib.request
    def _get_ok(url, timeout=5):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status < 500
        except Exception:  # noqa: BLE001
            return False
    # MCP base
    if not _get_ok(mcp_url.rstrip("/") + "/"):
        return False
    # each backend endpoint: a trivial ASK{}
    for ep in set(endpoints.values()):
        ask = ep + ("&" if "?" in ep else "?") + "query=ASK%7B%7D"
        if not _get_ok(ask):
            return False
    return True


def _errored_result(tc, be, endpoint, model, run_meta, exc, tb) -> dict:
    """A synthetic failed-case result for a case whose run_one raised, so the run
    summary still accounts for it (as an error) instead of silently dropping it."""
    kind = "case-crash" if _is_code_bug(exc) else "connection-error"
    return {
        "case_id": tc.id, "question": tc.question, "backend": be,
        "correct": False, "score_detail": f"{kind}: {type(exc).__name__}: {exc}",
        "error_category": kind, "subject_retries": 0,
        "got": None, "expected": tc.expected_for(be).values,
        "seconds": 0.0, "attempts": 0, "sparql_runs": 0,
        "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
        "tool_seconds_by_type": {}, "kpis": None, "final_sparql": None,
        "struggle_reason": None, "answer_raw": None, "answer": None,
        "transcript": [], "traceback": tb, "errored": True,
    }


def _write_error_record(live_path, tc, be, endpoint, model, run_meta, exc, tb):
    """Overwrite the loose live JSON with a TERMINAL `error` status + the full
    traceback, so the observer stops showing this case as forever-"running" and a
    human can see exactly why it died."""
    if live_path is None:
        return
    kind = "case-crash" if _is_code_bug(exc) else "connection-error"
    try:
        live_path.write_text(json.dumps({
            "case_id": tc.id, "backend": be, "status": "error",
            "error_category": kind,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": tb,
            "question": tc.question,
            "expected": (lambda _e: {"values": _e.values, "unit": _e.unit,
                         "labels": _e.labels,
                         "uncertainties": _e.uncertainties,
                         "score_uncertainty": _e.score_uncertainty,
                         "routes": _e.routes or None})(tc.expected_for(be)),
            "endpoint": endpoint, "model": model, "run_meta": run_meta or {},
            "correct": False, "answer": None, "conversation": [],
        }, indent=2, default=str))
    except Exception:  # noqa: BLE001
        pass


async def _open_mcp_session(mcp_url):
    """Open the MCP streamable-http transport + an initialised ClientSession on a
    fresh AsyncExitStack, returning (session, tools_schema, stack). The caller owns
    the stack: `await stack.aclose()` to tear the connection down (e.g. before a
    reconnect after the laptop suspended and the stream died). Done manually rather
    than via nested `async with` so the session can be re-opened mid-run."""
    from contextlib import AsyncExitStack
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    stack = AsyncExitStack()
    try:
        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(mcp_url))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools_schema = await mcp_tools_to_ollama(session)
        return session, tools_schema, stack
    except BaseException:
        await stack.aclose()
        raise


async def main_async(args) -> int:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    # Resolve the LLM client (BYOK/BYOM): --provider/--model select an Ollama,
    # Anthropic, OpenAI-compatible, Copilot or Gemini backend; a bare tag stays
    # Ollama (the default). Any API key is read from env at launch, never stored.
    llm_client, model_src, provider = resolve_llm(
        args.model, args.provider, ollama_host=args.ollama_host)
    model = llm_client.model
    # Escalation: a second, (usually better) LLM tried once when the primary
    # fails all its attempts on a configured backend.
    escalate_client = None
    escalate_model = None
    escalate_provider = None
    escalate_backends: set[str] = set()
    if args.escalate_provider and args.escalate_backends:
        escalate_backends = {b.strip() for b in args.escalate_backends.split(",") if b.strip()}
        if escalate_backends:
            escalate_client, esc_src, escalate_provider = resolve_llm(
                args.escalate_model, args.escalate_provider,
                ollama_host=args.ollama_host)
            escalate_model = escalate_client.model
            print(f"# escalation: {esc_src} on backends: {sorted(escalate_backends)}")
    endpoints = dict(DEFAULT_ENDPOINTS)
    if args.endpoint:
        for pair in args.endpoint:
            k, v = pair.split("=", 1)
            endpoints[k] = v
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    # ASK MODE is a HARD CUT from the experiment/suite path: take the FIRST grounded
    # answer (no scoring, no corrective re-prompting). It is requested EXPLICITLY by
    # --accept-first (the ask runner sets it) and also inferred for a bare --ask with
    # no --ask-expected. A --ask WITH --ask-expected is a one-off SCORED case, NOT ask
    # mode (the experiment behaviour: re-prompt on a wrong answer).
    ask_mode = bool(args.accept_first) or (bool(args.ask) and not args.ask_expected)
    if args.ask:
        # Ad-hoc single question. --ask-expected (JSON, same shape as a YAML case's
        # `expected:`) makes it SCORED like a library case, but it lives only as a
        # transcript, never written to the library (no orphan case).
        exp = Expected(values=[], unit="", labels=[])
        if args.ask_expected:
            exp = Expected.from_yaml(json.loads(args.ask_expected))
        cases = [TestCase(id=str(args.ask_id), question=str(args.ask),
                          expected=exp, backends=None)]
    else:
        cases = load_testcases(Path(args.testcases))

    # --only <id>[,<id>...]: run JUST these case id(s) from the testcases file. Used
    # by the observer's per-case RE-RUN (re-run one errored case into the same run
    # folder, with --keep-live so the others' transcripts are untouched).
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        cases = [c for c in cases if c.id in want]
        if not cases:
            ap_err = f"--only matched no case id in {args.testcases} (wanted {sorted(want)})"
            print(f"# {ap_err}", flush=True)
            sys.exit(2)

    # In ask mode the first answer counts — never spend more than one attempt.
    if ask_mode:
        args.max_attempts = 1

    print(f"# llm:   {model_src}")
    print(f"# mcp:   {args.mcp}")
    print(f"# backends: {backends}")
    print(f"# endpoints: {json.dumps(endpoints)}")
    print(f"# cases: {len(cases)}\n", flush=True)

    # run_meta: the model/provider + the loop params this whole run used. Stamped
    # into EVERY live + completed transcript so the observer can always show which
    # model answered and under what budget — no secrets (provider descriptor only).
    run_meta = {
        "provider": provider,
        "model": model,
        "runtime": llm_client.describe(),
        "max_steps": args.max_steps,
        "max_attempts": args.max_attempts,
        "token_budget": args.token_budget,
        "timeout": args.timeout,
        "skills": bool(args.skills),
    }

    results = []

    # Full per-test transcripts stored COMPRESSED in one zip (composition runs
    # dump large result blobs), one JSON entry per (case,backend) written
    # incrementally so a Ctrl-C keeps what ran. Light results JSON = metrics only.
    import zipfile
    zpath = Path(args.transcripts)
    zf = zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9)
    n_tx = 0
    # The zip is unreadable mid-run, so each transcript is also dropped as a loose
    # .json in the ONE canonical live dir (resolved identically here and in the
    # observer). Precedence: --live-dir > $BENCH_LIVE_DIR > DEFAULT_LIVE_DIR.
    live_dir = Path(resolve_live_dir(args.live_dir))
    live_dir.mkdir(parents=True, exist_ok=True)
    # RESUME (--continue): keep the dir, and pre-scan which (case,backend) already
    # reached a verdict so we can SKIP them below. A file counts as "done" only if
    # it carries correct=true/false (a real scored verdict); a file still at
    # status running/interrupted/error is NOT done and gets re-run. Anything missing
    # is simply absent here → re-run.
    done_pairs: set[tuple[str, str]] = set()
    if args.resume:
        for f in live_dir.glob("*.json"):
            try:
                rec = json.loads(f.read_text())
            except Exception:  # noqa: BLE001 — mid-write / garbage: treat as not-done
                continue
            if isinstance(rec.get("correct"), bool):
                cid, be = rec.get("case_id"), rec.get("backend")
                if cid and be:
                    done_pairs.add((cid, be))
        print(f"# --continue: {len(done_pairs)} case(s) already done, will skip them")
    # CLEAR stale *.json from a PREVIOUS run first (the observer reads every *.json,
    # so leftovers show as "stale runs that won't update"); each run owns the dir.
    # Only our own loose transcripts are removed. meta.json is NOT a transcript — it
    # is the run's metadata (e.g. an ask folder's question/pid) and is preserved.
    # --keep-live (and --continue) append instead of clearing.
    elif not args.keep_live:
        stale = [f for f in live_dir.glob("*.json") if f.name != "meta.json"]
        for f in stale:
            f.unlink()
        if stale:
            print(f"# cleared {len(stale)} stale transcript(s) from {live_dir}")
    print(f"# live transcripts -> {live_dir}  (observer reads BENCH_LIVE_DIR; "
          f"this is the canonical default)")

    # Open the MCP session manually (not via nested `async with`) so it can be
    # RE-OPENED mid-run: if the laptop suspends and the streamable-http stream dies,
    # we tear the dead session down and reconnect rather than crashing the run.
    session, tools_schema, mcp_stack = await _open_mcp_session(args.mcp)

    async def _reconnect_mcp():
        """Tear down the dead MCP session and open a fresh one; returns the new
        (session, tools_schema). Best-effort close of the old stack — a half-dead
        transport may itself error on close, which we ignore."""
        nonlocal mcp_stack
        try:
            await mcp_stack.aclose()
        except BaseException:  # noqa: BLE001
            pass
        new_session, new_tools, new_stack = await _open_mcp_session(args.mcp)
        mcp_stack = new_stack
        print("# MCP session RECONNECTED after a transport drop", flush=True)
        return new_session, new_tools

    try:
        print(f"# MCP tools: {[t['function']['name'] for t in tools_schema]}",
              flush=True)
        # When --skills is on, nudge the model that callable SKILLS exist
        # (the list_skills / get_skill MCP tools). The skills are METHOD
        # guidance the model pulls in on demand; never an answer.
        skill_text = None
        if args.skills:
            have = {t["function"]["name"] for t in tools_schema}
            if {"list_skills", "get_skill"} <= have:
                skill_text = (
                    "SKILLS: named how-to procedures are available via "
                    "list_skills / get_skill(<id>). They teach the METHOD, "
                    "never the answer.\n"
                    "MANDATORY FIRST STEP — resolve the class: the question "
                    "NAMES OR DESCRIBES a thing (a product, component, "
                    "material or element) but you must find WHICH class it "
                    "is. Before writing any data query you MUST get_skill("
                    "\"resolve-class\") and follow it to pick the BEST-FITTING "
                    "class IRI. A plain-English term often matches several "
                    "classes that hold DIFFERENT values — a broad roll-up and "
                    "the more specific members under it; read each candidate's "
                    "rdfs:comment and, when the question pins something more "
                    "specific, resolve THAT more granular class, not the broad "
                    "roll-up. Picking the wrong class is the "
                    "most common way to get a confidently-wrong number. Then "
                    "use the other skills for the method (instances of a "
                    "class, aggregating across the tree, units, absolute "
                    "mass = itemMass × fraction).")
                print("# skills: ENABLED (list_skills/get_skill present)",
                      flush=True)
            else:
                print("# skills: requested but list_skills/get_skill not "
                      "on the MCP server — run the skills-enabled MCP",
                      flush=True)
        print(f"# transcripts -> {zpath} (zip)\n", flush=True)

        # Run ALL cases of one backend before moving to the next backend
        # (so `--backends fq,composition` does every fq case first, then
        # every composition case) rather than interleaving per case.
        for be in backends:
            print(f"\n#### BACKEND: {be} "
                  f"({sum(1 for tc in cases if be in (tc.backends or backends))} "
                  f"case(s)) ####\n", flush=True)
            # Fetch the backend's primary skill once and inject it verbatim into
            # every attempt's system prompt — so the model has it without needing
            # to call get_skill() for it.
            be_skill_text = skill_text
            if args.skills and skill_text and BACKEND_MAIN_SKILL.get(be):
                main_skill_id = BACKEND_MAIN_SKILL[be]
                try:
                    have = {t["function"]["name"] for t in tools_schema}
                    endpoint_url_for_be = endpoints.get(be, "")
                    if "get_skill" in have and endpoint_url_for_be:
                        result = await session.call_tool(
                            "get_skill",
                            {"skill_id": main_skill_id,
                             "endpoint_url": endpoint_url_for_be})
                        main_skill_content = "\n".join(
                            c.text for c in result.content
                            if hasattr(c, "text"))
                        if main_skill_content.strip():
                            be_skill_text = (
                                f"{skill_text}\n\n"
                                f"PRIMARY ENDPOINT SKILL (`{main_skill_id}`) — "
                                f"read before writing any query:\n"
                                f"{main_skill_content}")
                            print(f"# injected main skill `{main_skill_id}` "
                                  f"({len(main_skill_content)} chars)", flush=True)
                except Exception as exc:
                    print(f"# WARNING: could not fetch main skill `{main_skill_id}`: {exc}",
                          flush=True)
            for tc in cases:
                use = tc.backends or backends
                if be not in use:
                    continue
                if be not in endpoints:
                    print(f"   ! no endpoint configured for backend {be}")
                    continue
                if (tc.id, be) in done_pairs:
                    # --continue: this (case,backend) already has a verdict on disk;
                    # leave its transcript untouched and move on.
                    print(f"== {tc.id} [{be}]: SKIP (already done, --continue)")
                    continue
                print(f"== {tc.id}: {tc.question}")
                live_path = live_dir / f"{tc.id}__{be}.json"
                r = None
                # SURVIVABLE per-case execution. Try the case; a RETRYABLE failure
                # (transport drop, cancellation, transient system error, a NETWORK
                # DROP / provider outage — anything that is NOT a deterministic code
                # bug) is NEVER abandoned: wait an escalating delay (30s → 2min →
                # 10min → then every 30min FOREVER), RE-CHECK the whole system is back
                # (MCP + every SPARQL endpoint answering), RECONNECT the MCP, and
                # RETRY the case from scratch. The run just PAUSES on an outage and
                # resumes when the network returns. Only a deterministic code bug is
                # isolated immediately (retrying it is pointless), so the run finishes.
                _try = 0
                while True:
                    try:
                        r = await run_one(
                            session, llm_client, model, tools_schema, tc, be,
                            endpoints[be], args.max_steps, args.verbose,
                            deadline_seconds=args.timeout,
                            token_budget=args.token_budget,
                            skill_text=be_skill_text,
                            max_attempts=args.max_attempts,
                            live_path=live_path,
                            run_meta=run_meta,
                            accept_first=ask_mode)
                        # A provider/network failure comes back as a RESULT (run_one
                        # swallows the chat exception), so the loop would otherwise
                        # record it and move on. Re-raise it so this same retry loop
                        # waits for the network to come back and RETRIES the case —
                        # which is what "realize it stopped and keep retrying" means.
                        if r.get("provider_error"):
                            raise ProviderUnavailable(
                                r.get("error") or r.get("score_detail")
                                or "provider unavailable")
                        break
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except BaseException as exc:   # noqa: BLE001
                        tb = traceback.format_exc()
                        permanent = _is_permanent_provider_failure(exc)
                        if _is_code_bug(exc) or permanent:
                            # ISOLATE immediately — a deterministic code bug, OR a
                            # PERMANENT provider failure (model retired/unknown, bad
                            # key/auth) that will NEVER recover, so retrying forever
                            # would just hang the run. The user must switch model/key.
                            why = ("permanent provider failure — switch the model/key"
                                   if permanent else "code bug")
                            print(f"   ✗ {be:12s} CASE ERRORED ({why} — not retried) "
                                  f"— continuing\n{tb}", flush=True)
                            r = _errored_result(tc, be, endpoints[be], model,
                                                run_meta, exc, tb)
                            _write_error_record(live_path, tc, be, endpoints[be],
                                                model, run_meta, exc, tb)
                            break
                        # RETRYABLE: never give up. Escalate the delay through the
                        # schedule, then hold at its last value (30min) forever.
                        delay = (_RETRY_DELAYS[_try] if _try < len(_RETRY_DELAYS)
                                 else _RETRY_FOREVER_EVERY)
                        _try += 1
                        print(f"   … {be} case failed ({type(exc).__name__}: "
                              f"{str(exc)[:80]}) — retry {_try} in {delay}s after "
                              f"the system recovers (will keep retrying)", flush=True)
                        await asyncio.sleep(delay)
                        # wait until the system is healthy again (MCP + endpoints),
                        # re-checking on the same cadence so a long outage is survived
                        # without busy-spinning.
                        while not await _system_healthy(args.mcp, endpoints):
                            print(f"   … system still not healthy; waiting {delay}s "
                                  f"more", flush=True)
                            await asyncio.sleep(delay)
                        try:
                            session, tools_schema = await _reconnect_mcp()
                        except BaseException as rexc:  # noqa: BLE001
                            print(f"   … reconnect not ready yet "
                                  f"({type(rexc).__name__}); will retry", flush=True)
                        # loop continues -> retry the case
                if r is None or r.get("errored"):
                    if r is not None:
                        results.append(r)
                    continue
                # ESCALATION: the primary model failed every attempt on this
                # backend → try once with the escalation model (one attempt,
                # same deadline + budget). The escalated result wins if correct;
                # the primary result is kept otherwise. The live transcript is
                # overwritten with the winning result so the observer shows it.
                if (r.get("correct") is False and not r.get("provider_error")
                        and escalate_client is not None
                        and be in escalate_backends):
                    esc_run_meta = {
                        "provider": escalate_provider,
                        "model": escalate_model,
                        "runtime": escalate_client.describe(),
                        "max_steps": args.max_steps,
                        "max_attempts": 1,
                        "token_budget": args.token_budget,
                        "timeout": args.timeout,
                        "skills": bool(args.skills),
                        "escalated": True,
                    }
                    print(f"   ↑ {be:12s} escalating to {escalate_provider}:{escalate_model}",
                          flush=True)
                    try:
                        r_esc = await run_one(
                            session, escalate_client, escalate_model,
                            tools_schema, tc, be, endpoints[be],
                            args.max_steps, args.verbose,
                            deadline_seconds=args.timeout,
                            token_budget=args.token_budget,
                            skill_text=be_skill_text,
                            max_attempts=1,
                            live_path=live_path,
                            run_meta=esc_run_meta)
                        mark_esc = "✓" if r_esc.get("correct") else "✗"
                        print(f"   {mark_esc} {be:12s} escalated: "
                              f"got={r_esc.get('got')} [{r_esc.get('score_detail','')}]",
                              flush=True)
                        # tag whichever result wins so the observer can show it
                        r["escalated_result"] = {
                            "provider": escalate_provider,
                            "model": escalate_model,
                            "correct": r_esc.get("correct"),
                            "score_detail": r_esc.get("score_detail"),
                            "got": r_esc.get("got"),
                        }
                        if r_esc.get("correct"):
                            # escalation succeeded — use this result, mark it
                            r_esc["escalated_from"] = {
                                "provider": provider, "model": model,
                                "correct": r.get("correct"),
                                "score_detail": r.get("score_detail"),
                            }
                            r = r_esc
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except BaseException as exc:  # noqa: BLE001
                        print(f"   ! {be} escalation failed ({type(exc).__name__}: "
                              f"{str(exc)[:80]}) — keeping primary result", flush=True)
                r["case_id"] = tc.id
                r["question"] = tc.question
                # peel the transcript off into the zip; keep results light
                transcript = r.pop("transcript", [])
                entry = {
                    "case_id": tc.id, "backend": be,
                    "question": tc.question,
                    # include the ground-truth uncertainties (a __valunc
                    # case scores them) so the observer can render the ± on
                    # the EXPECTED box, not just the value.
                    "expected": (lambda _e: {"values": _e.values,
                                 "unit": _e.unit,
                                 "labels": _e.labels,
                                 "uncertainties": _e.uncertainties,
                                 "score_uncertainty": _e.score_uncertainty,
                                 "routes": _e.routes or None})(tc.expected_for(be)),
                    "endpoint": endpoints[be],
                    "model": model,
                    "run_meta": run_meta,   # model/provider + loop params
                    "correct": r["correct"],
                    "score_detail": r["score_detail"],
                    # triaged error KIND (wrong-class / wrong-value / … ; ""
                    # when correct) + how many wrong-subject re-prompts fired
                    "error_category": r.get("error_category", ""),
                    "subject_retries": r.get("subject_retries", 0),
                    "got": r["got"],
                    "seconds": r["seconds"],
                    "attempts": r["attempts"],
                    "tokens_in": r["tokens_in"],
                    "tokens_out": r["tokens_out"],
                    "tokens_total": r["tokens_total"],
                    "kpis": r.get("kpis"),            # LOAD metrics
                    "final_sparql": r.get("final_sparql"),  # grounding query
                    "struggle_reason": r.get("struggle_reason"),  # why it failed (limit)
                    "answer": r["answer_raw"],
                    "conversation": transcript,
                }
                entry_json = json.dumps(entry, indent=2, default=str)
                zf.writestr(f"{tc.id}__{be}.json", entry_json)
                # also flush a loose copy so an in-progress run is readable
                (live_dir / f"{tc.id}__{be}.json").write_text(entry_json)
                n_tx += 1
                results.append(r)
                mark = "✓" if r["correct"] else "✗"
                cat = (f" «{r['error_category']}»"
                       if r.get("error_category") else "")
                to = (" TOKEN-CAP" if r.get("token_capped")
                      else " TIMEOUT" if r.get("timed_out") else "")
                dup = (f" dup×{r['duplicate_queries']}"
                       if r.get("duplicate_queries") else "")
                breakdown = " ".join(
                    f"{t.split('_')[0]}={v['seconds']:.1f}s"
                    for t, v in r["tool_seconds_by_type"].items())
                print(f"   {mark} {be:12s} {r['seconds']:6.1f}s  "
                      f"{r['attempts']} attempt(s)  {r['sparql_runs']} SPARQL{dup}  "
                      f"{r['tokens_total']} tok  "
                      f"got={r['got']} exp={r['expected']}  "
                      f"[{r['score_detail']}]{cat}{to}", flush=True)
                if breakdown:
                    print(f"        tool time: {breakdown}   "
                          f"tokens: in={r['tokens_in']} out={r['tokens_out']}",
                          flush=True)
                k = r.get("kpis") or {}
                if k:
                    print(f"        LOAD: wrong {k.get('wrong_queries',0)}/"
                          f"{k.get('queries_to_answer',0)} q "
                          f"(ratio {k.get('wrong_query_ratio',0)})  "
                          f"query-time {k.get('sparql_seconds',0)}s "
                          f"(avg {k.get('avg_query_seconds',0)}s, "
                          f"max {k.get('max_query_seconds',0)}s)  "
                          f"read {k.get('result_chars_read',0)}c", flush=True)
            print()
    finally:
        zf.close()
        # close the (possibly reconnected) MCP session/transport — best-effort, a
        # half-dead stream may itself error on close.
        try:
            await mcp_stack.aclose()
        except BaseException:  # noqa: BLE001
            pass
        print(f"# wrote {n_tx} transcript(s) -> {zpath} "
              f"({zpath.stat().st_size/1024:.0f} KB compressed)", flush=True)

    if args.resume and done_pairs:
        print(f"# --continue: {len(done_pairs)} previously-done case(s) preserved "
              f"on disk and SKIPPED this pass; the summary/results below cover only "
              f"the {len(results)} case(s) re-run now. The observer (which reads "
              f"every live *.json) shows the COMPLETE run.")
    print_summary(results, backends)
    if args.json:
        env = environment(model, args.ollama_host, endpoints, client=llm_client)
        payload = {"environment": env, "results": results}
        Path(args.json).write_text(json.dumps(payload, indent=2, default=str))
        print(f"# wrote {len(results)} results (+ environment) -> {args.json}")
        llm = env.get("llm", {})
        runtime = (llm.get("ollama_version")
                   and f"ollama {llm['ollama_version']}") or llm.get("provider")
        print(f"# environment: {runtime}  "
              f"model {llm.get('model')}  on {env.get('hardware', {}).get('cpu')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("testcases", nargs="?",
                    help="YAML file of test cases (see bench/testcases/)")
    ap.add_argument("--ask", default=None,
                    help="run a SINGLE ad-hoc natural-language question (no "
                         "expected answer, so it is not scored) against the "
                         "backends. Use instead of a testcases file.")
    ap.add_argument("--ask-id", default="ask",
                    help="case id to use for --ask (the live transcript file "
                         "is <ask-id>__<backend>.json)")
    ap.add_argument("--ask-expected", default=None,
                    help="optional JSON expected answer for --ask (same shape as "
                         "a YAML case's `expected:`, e.g. "
                         '\'{"unit":"kg","values":{"futuram:Iron":454}}\' or '
                         '\'{"names":["futuram:Iron"]}\'). Makes the ad-hoc run '
                         "SCORED without writing a case into the library.")
    ap.add_argument("--accept-first", dest="accept_first", action="store_true",
                    help="ASK MODE: take the FIRST grounded answer the model gives "
                         "(no scoring, no corrective re-prompting; max-attempts forced "
                         "to 1). Set by the ask runner; contrast the scored/experiment "
                         "path which re-prompts on a wrong answer.")
    ap.add_argument("--only", default=None,
                    help="run JUST these case id(s) from the testcases file "
                         "(comma-separated). Used by the observer's per-case re-run "
                         "(combine with --keep-live to overwrite only that case).")
    ap.add_argument("--live-dir", default=None,
                    help="directory for live per-(case,backend) JSON transcripts. "
                         f"Default: $BENCH_LIVE_DIR or {DEFAULT_LIVE_DIR} (the SAME "
                         "dir the observer website reads, so a run streams to the "
                         "page automatically). Override only for parallel runs.")
    ap.add_argument("--keep-live", action="store_true",
                    help="do NOT clear the live dir first; append this run's "
                         "transcripts to whatever is already there. Default is to "
                         "clear stale *.json so the dir only holds the current run.")
    ap.add_argument("--continue", dest="resume", action="store_true",
                    help="RESUME an interrupted run in this live dir: keep existing "
                         "transcripts and SKIP every (case,backend) that already "
                         "reached a verdict (correct/wrong); only re-run the ones "
                         "that are missing or left running/interrupted/errored "
                         "(e.g. after a network drop or a Stop). Implies --keep-live.")
    ap.add_argument("--backends", default="fq,composition",
                    help="comma-separated backend ids to run (default both)")
    ap.add_argument("--model", default=None,
                    help="model tag/name. For Ollama (default) a bare tag, "
                         "precedence --model > $BENCH_MODEL > DEFAULT_LLM_MODEL in "
                         "sparql-llm/.env. With --provider, overrides the profile's "
                         "default model. An inline provider/model tag also works, "
                         "e.g. --model anthropic/claude-opus-4-8.")
    ap.add_argument("--provider", default=None,
                    help="LLM provider profile from bench/providers.json (BYOK/BYOM): "
                         "ollama (default), anthropic, openai, openrouter, groq, "
                         "copilot, gemini, … The API key is read from the env var the "
                         "profile names (key_env) — never stored. Or set $BENCH_PROVIDER.")
    ap.add_argument("--login", default=None, metavar="PROVIDER",
                    help="run an interactive OAuth device-flow login for a provider "
                         "(currently: copilot/github) and cache the token, then exit. "
                         "Use this instead of pasting a token for GitHub Copilot.")
    ap.add_argument("--list-models", action="store_true",
                    help="list models available on the Ollama daemon (and provider "
                         "profiles) and exit")
    ap.add_argument("--list-provider-models", default=None, metavar="PROVIDER",
                    help="print (as JSON) the model ids a provider/profile offers "
                         "(for the UI model dropdown), then exit. e.g. "
                         "--list-provider-models copilot")
    ap.add_argument("--copilot-quota", action="store_true",
                    help="print (as JSON) the account's live Copilot quota "
                         "snapshot (plan, reset date, per-quota remaining), then "
                         "exit. Used by the observer to show the user their budget.")
    ap.add_argument("--mcp", default=DEFAULT_MCP_URL,
                    help=f"MCP server streamable-http URL (default {DEFAULT_MCP_URL})")
    ap.add_argument("--ollama-host", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                    help="Ollama daemon URL")
    ap.add_argument("--endpoint", action="append", default=[],
                    help="override an endpoint, e.g. fq=http://host/query/sparql (repeatable)")
    ap.add_argument("--max-steps", type=int, default=16,
                    help="max tool-loop steps per attempt (default 16). Enough room "
                         "to explore (search/class-resolution) AND query the data on "
                         "multi-part cases without running out mid-exploration. The "
                         "--timeout cap is the wall-clock backstop.")
    ap.add_argument("--max-attempts", type=int, default=1,
                    help="how many times the model may answer one (question, "
                         "backend). Default 1 = ONE attempt only, no re-prompt on "
                         "failure (a bad/ungrounded answer is scored as-is, not "
                         "retried). Raise to allow fair retries.")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="hard wall-clock budget per (question, backend), in "
                         "seconds (default 600 = 10 min). A case that cannot answer "
                         "within this is scored as a timeout — no case may run "
                         "longer. Raise only deliberately.")
    ap.add_argument("--temperature", type=float, default=None,
                    help="LLM sampling temperature for this run (default 0.0 = "
                         "deterministic). Set e.g. 0.1 to add exploration jitter. "
                         "Per-experiment; overrides the BENCH_TEMPERATURE env.")
    ap.add_argument("--token-budget", type=int, default=None,
                    help="TOTAL token ceiling per (question, backend) across all "
                         "attempts (prompt+generated). The primary limit: the "
                         "model stops once it has spent this many tokens. Unset = "
                         "no token cap (only --timeout applies).")
    ap.add_argument("--json", default=None,
                    help="write light per-run metrics (no transcripts) to this JSON")
    ap.add_argument("--transcripts", default="bench/transcripts.zip",
                    help="zip archive for the FULL per-test conversation "
                         "(one compressed <case>__<backend>.json per run; "
                         "default bench/transcripts.zip)")
    ap.add_argument("--skills", action="store_true",
                    help="tell the model that callable SKILLS (list_skills / "
                         "get_skill MCP tools) are available, so it can pull in "
                         "how-to procedures on demand. Needs the skills-enabled "
                         "MCP server (bench/mcp_with_skills.py).")
    ap.add_argument("--verbose", action="store_true", help="print each tool call")
    ap.add_argument("--escalate-provider", default=None, metavar="PROVIDER",
                    help="provider to escalate to when the primary model fails all "
                         "attempts (e.g. anthropic, copilot, openai). Requires "
                         "--escalate-backends.")
    ap.add_argument("--escalate-model", default=None, metavar="MODEL",
                    help="model for the escalation provider (blank = profile default).")
    ap.add_argument("--escalate-backends", default=None, metavar="BACKENDS",
                    help="comma-separated backends to apply escalation on "
                         "(e.g. fq,composition or just fq). Only cases on these "
                         "backends get an escalation retry on failure.")
    args = ap.parse_args()

    # Per-experiment LLM temperature: export it so benchlib.agent._temperature() reads
    # it at call time (the agent module is already imported). --temperature wins over
    # any inherited BENCH_TEMPERATURE; unset leaves the env (default 0.0) in place.
    if args.temperature is not None:
        os.environ["BENCH_TEMPERATURE"] = str(args.temperature)

    if args.login:
        from benchlib.oauth import login
        login(args.login)
        return 0
    if args.list_provider_models:
        models = provider_models(args.list_provider_models, args.ollama_host)
        print(json.dumps({"provider": args.list_provider_models, "models": models}))
        return 0
    if args.copilot_quota:
        print(json.dumps(copilot_quota_snapshot(load_providers())))
        return 0
    if args.list_models:
        return list_models(args.ollama_host)
    if not args.testcases and not args.ask:
        ap.error("testcases or --ask is required (or use --list-models)")
    # The MCP streamable-http session can drop with a transient httpx error on
    # establishment; retry the whole run a few times on a connection-level error so
    # one blip doesn't waste the launch (re-runs overwrite transcripts idempotently).
    import time
    import httpx

    def _is_conn_drop(exc):
        """True if `exc` is (or, for an ExceptionGroup, contains) a transient MCP
        connection-level error — anyio wraps the httpx error in an ExceptionGroup."""
        conn = (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError)
        if isinstance(exc, conn):
            return True
        if isinstance(exc, BaseExceptionGroup):
            return exc.split(conn)[0] is not None
        return False

    last_exc = None
    for attempt in range(1, 4):
        try:
            return asyncio.run(main_async(args))
        except KeyboardInterrupt:
            return 130
        except BaseException as exc:        # noqa: BLE001 - re-raise unless a conn drop
            if not _is_conn_drop(exc):
                raise
            last_exc = exc
            print(f"# MCP connection dropped ({exc!r}); retry {attempt}/3 in 5s…",
                  file=sys.stderr, flush=True)
            time.sleep(5)
    print(f"# bench aborted: MCP connection failed after 3 attempts: {last_exc}",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

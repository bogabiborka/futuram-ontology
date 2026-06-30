"use client";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Loader2, Code2, Copy, Check, HelpCircle, CloudOff, RotateCcw } from "lucide-react";
import { AnswerView, AnswerEditor, DiffView } from "./Answer.js";
import { ErrorBadge } from "./Legend.js";
import { BACKENDS, BE_COLOR, fmtTokens, fmtSecs, ST_BADGE } from "./format.js";
import { QueryTimeline } from "./Timeline.js";
import { Conversation } from "./Conversation.js";

export function CaseDetail({ cid, runs, details, onSaved, compare, hideExpected, experiment }) {
  const [editing, setEditing] = useState(false);
  const [rerunMsg, setRerunMsg] = useState("");
  const [rerunBusy, setRerunBusy] = useState(false);
  // EXPECTED is known from the testcases YAML BEFORE any run — fetch it directly so
  // the ground truth shows from the very beginning (no waiting for a backend to
  // complete and copy `expected` onto its run record). Falls back to the run record
  // only if the YAML lookup misses (e.g. an ad-hoc ask_ case not in any file).
  const [yamlExpected, setYamlExpected] = useState(null);
  const [yamlQuestion, setYamlQuestion] = useState(null);
  // per-backend expected overrides (some cases score a DIFFERENT IRI identity on each
  // backend); the default `yamlExpected` is shown in the top panel, while each backend
  // panel diffs against its OWN expected from this map (falling back to the default).
  const [expectedByBackend, setExpectedByBackend] = useState({});
  useEffect(() => {
    let live = true;
    if (!cid || String(cid).startsWith("ask_")) { setYamlExpected(null); setYamlQuestion(null); setExpectedByBackend({}); return; }
    fetch(`/api/expected?case_id=${encodeURIComponent(cid)}`)
      .then((r) => (r.ok ? r.json() : {}))
      .then((j) => { if (live) { setYamlExpected(j?.expected ?? null); setYamlQuestion(j?.question ?? null); setExpectedByBackend(j?.expected_by_backend ?? {}); } })
      .catch(() => { if (live) { setYamlExpected(null); setYamlQuestion(null); setExpectedByBackend({}); } });
    return () => { live = false; };
  }, [cid]);
  runs = runs || {};
  details = details || {};
  const allRuns = Object.values(runs);
  // Take expected / question from whichever run/detail has it — a still-running
  // backend has no `expected` yet, so don't restrict to the first run.
  const pick = (sel) => {
    for (const r of allRuns) {
      const v = sel(details[r?.id]) ?? sel(r);
      if (v != null && v !== "") return v;
    }
    return null;
  };
  // PREFER the YAML ground truth (available from the start); fall back to the run
  // record only when the YAML has no entry for this case.
  const expected = yamlExpected ?? pick((o) => o?.expected);
  const question = yamlQuestion ?? pick((o) => o?.question) ?? "";
  const scoreUnc = !!(expected && expected.score_uncertainty);
  const UNC_ADDON = " For each reported quantity, ALSO give its ± uncertainty (absolute, in the same unit).";
  // Mirror cases.TestCase.prompt_question: the harness appends the ± instruction
  // ONLY when the case scores uncertainty AND the question does not already ask for
  // it. A question whose verbatim text already requests the uncertainty (e.g. the
  // SI-5/6/7 ranking queries) must NOT show the add-on — it isn't asked twice.
  const showUncAddon = scoreUnc && !/uncertaint/i.test(question);
  // ASK MODE: ad-hoc question (id starts with "ask_") — determined solely by id prefix,
  // never by whether the run record happens to carry an expected shell (the bench writes
  // an empty expected into ask JSONs as a scoring artefact; ignore it here).
  const askMode = String(cid).startsWith("ask_");
  const canRerunCase = !!experiment && !askMode;
  const caseRunning = allRuns.some((r) => r?.status === "running");

  const rerunCase = async () => {
    if (!canRerunCase || rerunBusy) return;
    setRerunBusy(true); setRerunMsg("");
    try {
      const r = await fetch(
        `/api/experiments/${experiment}?action=rerun-case&case=${encodeURIComponent(cid)}`,
        { method: "POST" });
      const j = await r.json();
      setRerunMsg(r.ok ? "re-running this case…" : (j.error || "re-run failed"));
      if (r.ok) onSaved && onSaved();
    } catch (e) { setRerunMsg(String(e)); }
    finally { setRerunBusy(false); }
  };

  return (
    <div className="p-6 space-y-6 w-full">
      <header className="space-y-1 border-b pb-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-mono text-sm text-muted-foreground">{cid}</h2>
          {canRerunCase && (
            <div className="flex items-center gap-2 shrink-0">
              {rerunMsg && <span className="font-mono text-[11px] text-muted-foreground">{rerunMsg}</span>}
              <Button size="sm" variant="outline" className="h-7 gap-1.5 font-mono text-xs"
                onClick={rerunCase} disabled={rerunBusy || caseRunning}
                title="re-run only this case into the same run (leaves the others untouched)">
                {rerunBusy ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCcw className="size-3.5" />} re-run this case
              </Button>
            </div>
          )}
        </div>
        <p className="font-serif text-lg leading-snug">
          {question}
          {showUncAddon && (
            <span className="text-muted-foreground italic" title="auto-appended by the harness for uncertainty-scored cases (not part of the SI question)">
              {UNC_ADDON}
            </span>
          )}
        </p>
      </header>

      {hideExpected ? null : askMode ? (
        editing ? (
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                set an expected answer (turns this ask into a scored case)
              </CardTitle>
              <Button variant="outline" size="sm" className="h-7 font-mono text-xs" onClick={() => setEditing(false)}>done</Button>
            </CardHeader>
            <CardContent>
              <AnswerEditor caseId={cid} initial={null} onSaved={() => { setEditing(false); onSaved && onSaved(); }} />
            </CardContent>
          </Card>
        ) : (
          <div className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
            <span>ask mode — no ground truth; showing the model's answer.</span>
            <Button variant="ghost" size="sm" className="h-6 font-mono text-[11px]" onClick={() => setEditing(true)}>
              set expected to score it
            </Button>
          </div>
        )
      ) : (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
              expected (ground truth){Object.keys(expectedByBackend).length > 0 && <span className="ml-2 text-amber-600" title={`Per-backend overrides: ${Object.keys(expectedByBackend).join(", ")} — each backend panel scores against its own expected`}>⚠ per-backend overrides</span>}
            </CardTitle>
            <Button variant="outline" size="sm" className="h-7 font-mono text-xs" onClick={() => setEditing((e) => !e)}>
              {editing ? "done" : "set / edit"}
            </Button>
          </CardHeader>
          <CardContent>
            {editing ? (
              <AnswerEditor caseId={cid} initial={expected} onSaved={() => { setEditing(false); onSaved && onSaved(); }} />
            ) : Object.keys(expectedByBackend).length > 0 ? (
              // When per-backend overrides exist, show each backend's own expected
              // rather than the base (which has different IRI identity). The base is
              // shown only as a fallback label for backends without an override.
              <div className="space-y-2">
                {BACKENDS.map((be) => {
                  const beExp = expectedByBackend[be] ?? expected;
                  if (!beExp) return null;
                  return (
                    <div key={be}>
                      <div className={cn("font-mono text-[10px] uppercase tracking-wider mb-1", BE_COLOR[be])}>{be}</div>
                      <AnswerView ans={beExp} />
                    </div>
                  );
                })}
              </div>
            ) : expected ? (
              <AnswerView ans={expected} />
            ) : (
              <div className="font-mono text-xs text-muted-foreground italic">— no expected set — click "set / edit"</div>
            )}
          </CardContent>
        </Card>
      )}

      <div className="space-y-2">
        <div className="font-mono text-xs uppercase tracking-wider text-muted-foreground">result</div>
        <div className="grid grid-cols-2 gap-4 items-start">
          {BACKENDS.map((be) => (
            <div key={be} className="min-w-0">
              <BackendPanel backend={be} run={runs[be]} detail={details[runs[be]?.id]} expected={expectedByBackend?.[be] ?? expected} askMode={askMode} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function BackendPanel({ backend, run, detail, expected, askMode }) {
  if (!run) return (
    <Card className="opacity-60">
      <CardHeader className="pb-2">
        <CardTitle className={cn("font-mono text-sm", BE_COLOR[backend])}>
          {backend} <span className="text-muted-foreground font-normal">— not run</span>
        </CardTitle>
      </CardHeader>
    </Card>
  );
  const d = detail || run;
  const attempts = Array.isArray(d.conversation) ? d.conversation : [];
  const st = askMode
    ? (run.status === "running" ? { label: "working", variant: "secondary", cls: "" }
       : run.status === "error" ? { label: "error", variant: "secondary", cls: "" }
       : d.answer != null ? { label: "answered", variant: "secondary", cls: "" }
       : { label: "no answer", variant: "secondary", cls: "" })
    : ST_BADGE(run.status);
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className={cn("font-mono text-sm uppercase tracking-wider", BE_COLOR[backend])}>{backend}</CardTitle>
        <div className="flex items-center gap-1.5">
          {!askMode && (
            <ErrorBadge category={d.error_category || run.error_category}
                        retries={d.subject_retries || run.subject_retries} />
          )}
          <Badge variant={st.variant} className={cn("rounded-sm font-mono text-[11px]", st.cls)}>
            {run.status === "running" && <Loader2 className="size-3 animate-spin" />}
            {st.label}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <RunMeta meta={d.run_meta || run.run_meta} />
        <div className="flex gap-3 font-mono text-[11px] text-muted-foreground">
          <span>{fmtSecs(run.seconds)}</span><span>{fmtTokens(run.tokens)} tok</span>
          <span>{run.tool_calls}q</span><span>{run.attempts}att</span>
        </div>

        {d.answer != null ? (
          askMode ? <AnswerView ans={d.answer} /> : <DiffView got={d.answer} expected={expected} />
        ) : run.status === "error" ? (
          <CaseErrorBanner error={d.error || run.error} tb={d.traceback || run.traceback}
                           category={d.error_category || run.error_category} />
        ) : run.status === "running" ? (
          <div className="font-mono text-xs text-muted-foreground">working…</div>
        ) : (
          <div className="font-mono text-xs text-muted-foreground">
            {askMode ? "the model produced no answer" : (d.score_detail || "no answer")}
          </div>
        )}

        {(d.error_category || run.error_category) === "provider-error" && (
          <ProviderErrorBanner detail={d.score_detail} meta={d.run_meta || run.run_meta} />
        )}

        {(() => {
          const cat = d.error_category || run.error_category;
          const reason = d.struggle_reason || run.struggle_reason;
          if (cat === "provider-error") return null;
          if (askMode) {
            return (
              <>
                <FinalQuery sparql={d.final_sparql} />
                {reason && <StruggleReason text={reason} askMode />}
              </>
            );
          }
          const failed = run.status !== "running" && run.correct !== true;
          return failed
            ? <StruggleReason text={reason || "(the model gave no explanation)"} />
            : <FinalQuery sparql={d.final_sparql} />;
        })()}

        <QueryTimeline attempts={attempts} />
        <Conversation attempts={attempts} />
      </CardContent>
    </Card>
  );
}

function RunMeta({ meta }) {
  if (!meta || !meta.model) return null;
  const rt = meta.runtime || {};
  const where = rt.base_url || rt.host || null;
  const bits = [
    meta.max_steps != null && `steps ${meta.max_steps}`,
    meta.max_attempts != null && `attempts ${meta.max_attempts}`,
    meta.token_budget != null && `budget ${fmtTokens(meta.token_budget)}`,
    meta.timeout != null && `timeout ${meta.timeout}s`,
    meta.skills != null && (meta.skills ? "skills on" : "skills off"),
  ].filter(Boolean);
  return (
    <div className="flex flex-wrap items-center gap-1.5 font-mono text-[11px]">
      <Badge variant="secondary" className="rounded-sm font-mono text-[11px]">
        {meta.provider || rt.provider || "?"} · {meta.model}
      </Badge>
      {where && <span className="text-muted-foreground/70 truncate max-w-[16rem]" title={where}>{where}</span>}
      {bits.map((b) => <span key={b} className="text-muted-foreground">{b}</span>)}
    </div>
  );
}

function StruggleReason({ text, askMode }) {
  if (!text || !String(text).trim()) return null;
  const border = askMode ? "border-border" : "border-warn/40";
  const head = askMode ? "border-border bg-muted/40 text-muted-foreground"
                       : "border-warn/30 bg-warn/[0.07] text-warn";
  const bg = askMode ? "" : "bg-warn/[0.05]";
  return (
    <div className={cn("rounded-md border overflow-hidden", border, bg)}>
      <div className={cn("flex items-center gap-1.5 px-3 py-1.5 border-b font-mono text-[11px] font-semibold uppercase tracking-wider", head)}>
        <HelpCircle className="size-3.5" /> {askMode ? "the model's note" : "why the model failed"}
      </div>
      <p className="px-3 py-2 text-xs leading-relaxed text-foreground/90 whitespace-pre-wrap">
        {String(text).trim()}
      </p>
    </div>
  );
}

function ProviderErrorBanner({ detail, meta }) {
  const raw = String(detail || "").replace(/^chat failed:\s*/i, "").trim();
  const isRate = /rate limit|ratelimit|429|quota/i.test(raw);
  const model = meta?.model || "this model";
  return (
    <div className="rounded-md border border-destructive/50 bg-destructive/[0.06] overflow-hidden">
      <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-destructive/40 bg-destructive/[0.09] font-mono text-[11px] font-semibold uppercase tracking-wider text-destructive">
        <CloudOff className="size-3.5" /> provider failed — not a model mistake
      </div>
      <div className="px-3 py-2 text-xs leading-relaxed text-foreground/90 space-y-1.5">
        <p>
          {isRate
            ? <>The provider <span className="font-semibold">rate-limited</span> {model} — every call was rejected before the model could answer.</>
            : <>The provider rejected the call for <span className="font-mono">{model}</span>.</>}
        </p>
        <p className="font-semibold text-destructive">
          Switch the model above and re-run — pick a provider/model that isn't capped.
        </p>
        {raw && <p className="font-mono text-[11px] text-muted-foreground break-words">{raw}</p>}
      </div>
    </div>
  );
}

function CaseErrorBanner({ error, tb, category }) {
  const isConn = category === "connection-error";
  return (
    <div className="rounded-md border border-amber-500/50 bg-amber-500/[0.06] overflow-hidden">
      <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-amber-500/40 bg-amber-500/[0.10] font-mono text-[11px] font-semibold uppercase tracking-wider text-amber-700">
        <CloudOff className="size-3.5" />
        {isConn ? "connection dropped — case isolated, run continued"
                : "case crashed — isolated, run continued"}
      </div>
      <div className="px-3 py-2 text-xs leading-relaxed text-foreground/90 space-y-1.5">
        {error && <p className="font-mono text-[11px] text-destructive break-words">{error}</p>}
        {isConn && (
          <p className="text-muted-foreground">
            The MCP/model connection looked dropped (often a brief system suspend).
            The loop kept going; re-run this case if several show this.
          </p>
        )}
        {tb && (
          <pre className="mt-1 max-h-64 overflow-auto rounded bg-muted/50 p-2 font-mono text-[10px] leading-snug text-muted-foreground whitespace-pre-wrap break-words">{tb}</pre>
        )}
      </div>
    </div>
  );
}

function FinalQuery({ sparql }) {
  const [copied, setCopied] = useState(false);
  if (!sparql || !String(sparql).trim()) return null;
  const text = String(sparql).trim();
  const copy = () => {
    navigator.clipboard?.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="rounded-md border border-blue/40 bg-blue/[0.04] overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-blue/30 bg-blue/[0.06]">
        <span className="flex items-center gap-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-blue">
          <Code2 className="size-3.5" /> final answering query
        </span>
        <Button variant="ghost" size="sm" className="h-6 px-2 font-mono text-[11px] text-blue hover:bg-blue/10" onClick={copy}>
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? "copied" : "copy"}
        </Button>
      </div>
      <pre className="px-3 py-2 overflow-x-auto font-mono text-[11px] leading-relaxed text-foreground/90 whitespace-pre">
        {text}
      </pre>
    </div>
  );
}

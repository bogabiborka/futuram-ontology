import { useState } from "react";
import { DiffView, AnswerView } from "./Answer.jsx";
import { Timeline } from "./Timeline.jsx";
import { Conversation } from "./Conversation.jsx";
import { statusOf, fmtSec, fmtK, ERROR_LABELS, STATUS_LABEL } from "./format.js";
import { cn } from "./utils.js";

const BACKEND = {
  fq: {
    name: "Query-optimized (fq)",
    description: "Query-optimized dataset — every class already has computed mass values. Tests whether the model can navigate a clean vocabulary.",
    hdr: "border-blue/30 bg-blue/5",
    label: "text-blue",
  },
  composition: {
    name: "Baseline Composition",
    description: "Baseline composition dataset — the model must aggregate part-of hierarchies via SPARQL itself. Higher difficulty.",
    hdr: "border-warn/30 bg-warn/5",
    label: "text-warn",
  },
};

const STATUS_CLS = {
  correct:         "border-ok/40 bg-ok/10 text-ok",
  wrong:           "border-bad/40 bg-bad/10 text-bad",
  "no-answer":     "border-border bg-muted text-muted-foreground",
  timeout:         "border-warn/40 bg-warn/10 text-warn",
  "token-cap":     "border-warn/40 bg-warn/10 text-warn",
  "provider-error":"border-blue/40 bg-blue/10 text-blue",
  pending:         "border-border bg-muted text-muted-foreground",
};

function StatusBadge({ status }) {
  return (
    <span className={cn("inline-flex items-center rounded-sm border px-1.5 py-0.5 font-mono text-[11px] leading-none font-semibold", STATUS_CLS[status] || STATUS_CLS.pending)}>
      {STATUS_LABEL[status] || status}
    </span>
  );
}

function ErrorBadge({ category, retries }) {
  if (!category) return null;
  return (
    <span
      className="inline-flex items-center rounded-sm border border-bad/50 bg-bad/5 px-1.5 py-0.5 font-mono text-[11px] leading-none text-bad"
      title={ERROR_LABELS[category] || category}
    >
      {category}{retries ? ` · ${retries} re-prompt` : ""}
    </span>
  );
}

function Kpi({ label, value, tip }) {
  return (
    <div className="flex justify-between gap-3 py-0.5 text-xs" title={tip}>
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums font-mono">{value}</span>
    </div>
  );
}

function SparqlBlock({ sparql }) {
  const [copied, setCopied] = useState(false);
  const copy = () => { navigator.clipboard.writeText(sparql).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }); };
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">final SPARQL</span>
        <button onClick={copy} className="text-[11px] border border-border rounded px-2 py-0.5 cursor-pointer text-muted-foreground hover:text-foreground bg-transparent">
          {copied ? "copied!" : "copy"}
        </button>
      </div>
      <pre className="md-raw text-[11px]">{sparql}</pre>
    </div>
  );
}

function BackendPanel({ backend, data, expected }) {
  const info = BACKEND[backend] || { name: backend, description: "", hdr: "border-border bg-muted/20", label: "text-foreground" };
  const [showSparql, setShowSparql] = useState(false);
  if (!data) {
    return (
      <div className="border border-border rounded-md p-4 opacity-40">
        <div className={cn("font-semibold text-sm mb-1", info.label)}>{info.name}</div>
        <span className="text-xs text-muted-foreground">not run</span>
      </div>
    );
  }
  const status = statusOf(data);
  const kpis = data.kpis || {};
  return (
    <div className="border border-border rounded-md overflow-hidden flex flex-col">
      {/* Header */}
      <div className={cn("px-3.5 py-2.5 border-b border-border flex items-center justify-between flex-wrap gap-1.5", info.hdr)}>
        <div className="flex items-center gap-2">
          <span className={cn("font-bold text-sm", info.label)}>{info.name}</span>
          <StatusBadge status={status} />
          {data.error_category && <ErrorBadge category={data.error_category} retries={data.subject_retries} />}
        </div>
        <div className="font-mono text-[11px] text-muted-foreground">
          {fmtSec(data.seconds)} · {fmtK(data.tokens_out)} tok out · {data.attempts} attempt{data.attempts !== 1 ? "s" : ""}
        </div>
      </div>

      {/* Backend description */}
      <div className="px-3.5 py-2 border-b border-border text-xs text-muted-foreground bg-background/60">
        {info.description}
      </div>

      {/* Answer diff */}
      <div className="px-3.5 py-2.5 border-b border-border">
        <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground mb-1.5">
          answer {status === "correct" ? "✓" : status === "wrong" ? "✗" : ""}
        </div>
        {data.answer ? <DiffView got={data.answer} expected={expected} /> : <AnswerView ans={data.answer} />}
        {data.struggle_reason && (
          <div className="mt-2 text-xs text-blue bg-blue/5 border border-blue/20 rounded px-2.5 py-1.5">
            <span className="font-semibold">model explains: </span>{data.struggle_reason}
          </div>
        )}
      </div>

      {/* KPIs */}
      <div className="px-3.5 py-2.5 border-b border-border">
        <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground mb-1.5">
          efficiency
          <span className="normal-case tracking-normal font-normal ml-2">— steps taken to reach an answer</span>
        </div>
        <div className="grid grid-cols-2 gap-x-6">
          <Kpi label="SPARQL queries" value={kpis.queries_to_answer ?? "—"} tip="Queries run before final answer" />
          <Kpi label="tool calls" value={kpis.tool_calls_total ?? "—"} tip="Total tool invocations including skill reads" />
          <Kpi label="LLM time" value={kpis.llm_seconds != null ? fmtSec(kpis.llm_seconds) : "—"} tip="Wall time waiting for the model" />
          <Kpi label="tokens out" value={fmtK(data.tokens_out)} tip="Generated (completion) tokens" />
        </div>
      </div>

      {/* Timeline */}
      {data.timeline?.length > 0 && (
        <div className="px-3.5 py-2.5 border-b border-border">
          <Timeline timeline={data.timeline} />
        </div>
      )}

      {/* SPARQL toggle */}
      {data.final_sparql && (
        <div className="px-3.5 py-2.5 border-b border-border">
          <button
            onClick={() => setShowSparql((v) => !v)}
            className="text-xs text-muted-foreground hover:text-foreground bg-transparent border-none cursor-pointer underline underline-offset-2 p-0"
          >
            {showSparql ? "hide" : "show"} final SPARQL
          </button>
          {showSparql && <div className="mt-2"><SparqlBlock sparql={data.final_sparql} /></div>}
        </div>
      )}

      {/* Full conversation */}
      {data.conversation?.length > 0 && (
        <div className="px-3.5 py-2.5">
          <Conversation conversation={data.conversation} />
        </div>
      )}
    </div>
  );
}

export function CaseDetail({ group }) {
  if (!group) return (
    <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
      Select a question from the list
    </div>
  );
  const allBackends = Object.keys(group.backends);
  const hasMultiple = allBackends.length > 1;

  return (
    <div className="p-5 overflow-y-auto h-full">
      {/* Question */}
      <div className="mb-4">
        <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground mb-1.5">question</div>
        <div className="text-base font-semibold leading-snug">{group.question}</div>
      </div>

      {/* Expected */}
      {group.expected && (
        <div className="mb-5 bg-ok/5 border border-ok/20 rounded-md px-3.5 py-2.5">
          <div className="font-mono text-[10px] uppercase tracking-wider text-ok mb-1.5">ground truth (from SI document)</div>
          <AnswerView ans={group.expected} />
        </div>
      )}

      {/* Backend panels */}
      <div className={cn("grid gap-4", hasMultiple ? "grid-cols-2" : "grid-cols-1")}>
        {allBackends.map((b) => (
          group.backends[b] ? <BackendPanel key={b} backend={b} data={group.backends[b]} expected={group.expected} /> : null
        ))}
      </div>
    </div>
  );
}

import { useState, useEffect, useCallback, useMemo } from "react";
import { CaseDetail } from "./CaseDetail.jsx";
import { statusOf, groupCases, fmtK } from "./format.js";
import { cn } from "./utils.js";

const STATUS_CLS = {
  correct:          "border-ok/40 bg-ok/10 text-ok",
  wrong:            "border-bad/40 bg-bad/10 text-bad",
  "no-answer":      "border-border bg-muted text-muted-foreground",
  timeout:          "border-warn/40 bg-warn/10 text-warn",
  "token-cap":      "border-warn/40 bg-warn/10 text-warn",
  "provider-error": "border-blue/40 bg-blue/10 text-blue",
  pending:          "border-border bg-muted text-muted-foreground",
};
const STATUS_SHORT = {
  correct: "✓", wrong: "✗", "no-answer": "○",
  timeout: "⏱", "token-cap": "⊘", "provider-error": "⚡", pending: "·",
};

// ── Skeleton ──────────────────────────────────────────────────────────────────

function Skeleton() {
  return (
    <div className="flex flex-col h-screen overflow-hidden" aria-busy="true" aria-label="Loading benchmark data">
      <div className="px-5 py-3 bg-primary h-[60px]" />
      <div className="px-5 py-3 bg-card border-b border-border h-[52px]" />
      <div className="flex flex-1 overflow-hidden">
        <div className="w-72 flex-shrink-0 border-r border-sidebar-border bg-sidebar flex flex-col">
          <div className="px-4 py-3 border-b border-sidebar-border space-y-3">
            {[70, 55].map((w, i) => (
              <div key={i}>
                <div className="skeleton h-2 w-16 mb-1.5" />
                <div className="skeleton h-1.5 rounded-full" style={{ width: `${w}%` }} />
              </div>
            ))}
          </div>
          <div className="flex-1 divide-y divide-border/50">
            {Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="px-3 py-3.5 space-y-2">
                <div className="skeleton h-2.5 rounded w-full" />
                <div className="skeleton h-2 rounded w-3/4" />
                <div className="flex gap-1.5">
                  <div className="skeleton h-4 w-12 rounded-sm" />
                  <div className="skeleton h-4 w-12 rounded-sm" />
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="flex-1 flex items-center justify-center bg-background">
          <div className="text-sm text-muted-foreground animate-pulse">Loading benchmark data…</div>
        </div>
      </div>
    </div>
  );
}

// ── Scoreboard ────────────────────────────────────────────────────────────────

function Scoreboard({ groups, backends }) {
  const bColor = { fq: "bg-blue", composition: "bg-warn" };
  const bLabel = { fq: "Query-optimized (fq)", composition: "Baseline" };
  return (
    <div className="px-4 py-3 border-b border-sidebar-border bg-sidebar">
      <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground mb-2.5">
        score by backend
      </div>
      <div className="space-y-2.5">
        {backends.map((b) => {
          let correct = 0, total = 0;
          for (const g of groups) {
            if (g.backends[b]) { total++; if (g.backends[b].correct) correct++; }
          }
          const pct = total ? Math.round((correct / total) * 100) : 0;
          return (
            <div key={b}>
              <div className="flex justify-between text-xs mb-1">
                <span className={cn("font-semibold", b === "fq" ? "text-blue" : "text-warn")}>
                  {bLabel[b] || b}
                </span>
                <span className="tabular-nums text-muted-foreground">{correct}/{total} · {pct}%</span>
              </div>
              <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className={cn("h-full rounded-full transition-all duration-500", bColor[b] || "bg-primary")}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Case row ──────────────────────────────────────────────────────────────────

function CaseRow({ group, backends, selected, index, onClick }) {
  return (
    <button
      onClick={onClick}
      role="option"
      aria-selected={selected}
      aria-label={`Question ${index + 1}`}
      className={cn(
        "w-full text-left px-3 py-3.5 border-b border-border/40 cursor-pointer flex flex-col gap-2",
        "transition-colors duration-150 min-h-[44px]",
        selected
          ? "bg-blue/8 border-l-[3px] border-l-blue"
          : "hover:bg-accent/60 active:bg-accent bg-transparent border-l-[3px] border-l-transparent"
      )}
    >
      <div className={cn(
        "text-xs leading-relaxed line-clamp-2",
        selected ? "font-semibold text-foreground" : "text-foreground/80"
      )}>
        {group.question}
      </div>
      <div className="flex gap-1.5 flex-wrap">
        {backends.map((b) => {
          const status = statusOf(group.backends[b]);
          const data = group.backends[b];
          return (
            <span
              key={b}
              className={cn(
                "text-[10px] px-1.5 py-0.5 rounded-sm border font-mono leading-none transition-colors duration-150",
                STATUS_CLS[status] || STATUS_CLS.pending
              )}
              title={data?.tokens_out ? `${fmtK(data.tokens_out)} tokens out` : undefined}
            >
              {b === "fq" ? "fq" : "comp"}: {STATUS_SHORT[status] || "·"}
            </span>
          );
        })}
      </div>
    </button>
  );
}

// ── Header ────────────────────────────────────────────────────────────────────

function Header({ config, run, selected, total, onPrev, onNext }) {
  const env = run?.environment;
  const model = env?.llm?.model || config?.model || "unknown";
  const provider = env?.llm?.provider || config?.provider || "";
  const ts = run?.ts
    ? new Date(run.ts.replace(/T(\d{2})-(\d{2})-(\d{2})-\d+Z$/, "T$1:$2:$3Z")).toLocaleString()
    : null;

  return (
    <div className="px-5 py-3 bg-primary text-primary-foreground flex items-center justify-between gap-4 flex-wrap">
      <div>
        <div className="text-base font-bold tracking-tight">FutuRaM Benchmark Viewer</div>
        <div className="text-xs text-primary-foreground/60 mt-0.5">
          Can an LLM answer material questions by writing SPARQL?
        </div>
      </div>
      <div className="flex items-center gap-5">
        <div className="flex gap-5 text-xs text-primary-foreground/60">
          {model && (
            <div>
              <div className="font-semibold text-primary-foreground/80">model</div>
              <div className="font-mono">{provider ? `${provider}:` : ""}{model}</div>
            </div>
          )}
          {ts && (
            <div>
              <div className="font-semibold text-primary-foreground/80">run</div>
              <div>{ts}</div>
            </div>
          )}
        </div>
        {total > 0 && (
          <div className="flex items-center gap-1.5 text-xs text-primary-foreground/70">
            <button
              onClick={onPrev}
              disabled={selected === null || selected === 0}
              aria-label="Previous question"
              title="Previous question (←)"
              className="px-2 py-1 rounded border border-primary-foreground/20 hover:bg-primary-foreground/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors bg-transparent text-primary-foreground cursor-pointer"
            >←</button>
            <span className="tabular-nums min-w-[5rem] text-center">
              {selected !== null ? `${selected + 1} / ${total}` : `${total} questions`}
            </span>
            <button
              onClick={onNext}
              disabled={selected === null || selected === total - 1}
              aria-label="Next question"
              title="Next question (→)"
              className="px-2 py-1 rounded border border-primary-foreground/20 hover:bg-primary-foreground/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors bg-transparent text-primary-foreground cursor-pointer"
            >→</button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Narrative strip ───────────────────────────────────────────────────────────

function Narrative({ config, groups }) {
  const maxTok = config?.tokenBudget ? fmtK(config.tokenBudget) : "—";
  return (
    <div className="px-5 py-2 bg-card border-b border-border text-xs text-muted-foreground flex items-center justify-between gap-4 flex-wrap leading-relaxed">
      <span>
        <strong className="text-foreground">{groups.length} questions</strong> about
        end-of-life vehicle materials — asked to{" "}
        <span className="font-mono text-foreground">{config?.model || "an LLM"}</span> against two
        SPARQL backends. Ground truth from the SI document.
        {config?.note && <em className="ml-1">{config.note}</em>}
      </span>
      <span className="whitespace-nowrap flex-shrink-0">
        {config?.maxAttempts ?? "?"} attempts · {config?.maxSteps ?? "?"} steps · {maxTok} tok
      </span>
    </div>
  );
}

// ── Welcome pane ──────────────────────────────────────────────────────────────

function answerSummary(ans) {
  if (!ans) return null;
  const unit = ans.unit ? ` ${ans.unit}` : "";
  // Scalar
  if (ans.value != null) {
    const unc = ans.uncertainty != null ? ` ± ${ans.uncertainty}${unit}` : "";
    return `${ans.value}${unit}${unc}`;
  }
  // List of values
  if (ans.values?.length) {
    const localName = (s) => s ? String(s).split(/[#/]/).pop().replace(/_Y\d+$/, "") : null;
    const parts = ans.values.slice(0, 3).map((v, i) => {
      const lbl = ans.labels?.[i] ? localName(ans.labels[i]) : null;
      const unc = ans.uncertainties?.[i] != null ? ` ± ${ans.uncertainties[i]}` : "";
      return lbl ? `${lbl}: ${v}${unc}${unit}` : `${v}${unc}${unit}`;
    });
    return parts.join(" · ") + (ans.values.length > 3 ? ` · +${ans.values.length - 3} more` : "");
  }
  if (ans.names?.length) return ans.names.join(", ");
  return null;
}

function WelcomePane({ groups, backends, onSelect }) {
  let correct = 0;
  for (const g of groups) {
    for (const b of backends) {
      if (g.backends[b]?.correct) { correct++; break; }
    }
  }
  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-2xl mx-auto px-8 py-8">
        {/* Intro */}
        <div className="mb-6">
          <h2 className="text-xl font-bold mb-1">Benchmark overview</h2>
          <p className="text-sm text-muted-foreground leading-relaxed">
            {groups.length} questions from the SI document — the model had to write SPARQL
            to look up the answer, not recall memorised facts.{" "}
            <strong className="text-foreground">{correct} of {groups.length}</strong> answered correctly
            across at least one backend. Click any card to read the full reasoning transcript.
          </p>
        </div>

        {/* Summary cards */}
        <div className="space-y-2">
          {groups.map((g, i) => {
            const statuses = backends.map((b) => ({ b, status: statusOf(g.backends[b]) }));
            const anyCorrect = statuses.some((s) => s.status === "correct");
            const expected = answerSummary(g.expected);
            return (
              <button
                key={g.case_id}
                onClick={() => onSelect(i)}
                className={cn(
                  "w-full text-left px-4 py-3 rounded-md border flex items-start gap-4",
                  "hover:border-border hover:bg-accent/50 transition-colors duration-150 bg-card cursor-pointer",
                  anyCorrect ? "border-ok/30" : "border-border"
                )}
              >
                {/* Index */}
                <span className="font-mono text-xs text-muted-foreground mt-0.5 w-5 flex-shrink-0 text-right select-none">
                  {i + 1}
                </span>

                {/* Content */}
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="text-sm leading-snug font-medium">{g.question}</div>
                  {expected && (
                    <div className="font-mono text-[11px] text-muted-foreground truncate" title={expected}>
                      ↳ {expected}
                    </div>
                  )}
                </div>

                {/* Status pills */}
                <div className="flex gap-1.5 flex-shrink-0 mt-0.5">
                  {statuses.map(({ b, status }) => (
                    <span
                      key={b}
                      className={cn(
                        "text-[10px] px-1.5 py-0.5 rounded-sm border font-mono leading-none",
                        STATUS_CLS[status] || STATUS_CLS.pending
                      )}
                      title={b}
                    >
                      {b === "fq" ? "fq" : "comp"}: {STATUS_SHORT[status] || "·"}
                    </span>
                  ))}
                </div>
              </button>
            );
          })}
        </div>

        <p className="mt-5 text-xs text-muted-foreground text-center">
          Press <kbd className="font-mono bg-muted border border-border rounded px-1.5 py-0.5">→</kbd> to step through questions in order
        </p>
      </div>
    </div>
  );
}

// ── Root App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    fetch("./data.json")
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  const groups = useMemo(() => (data ? groupCases(data.run.cases) : []), [data]);
  const backends = useMemo(() => data?.config?.backends || ["fq"], [data]);

  const goPrev = useCallback(() => setSelected((s) => Math.max(0, (s ?? 1) - 1)), []);
  const goNext = useCallback(() => setSelected((s) => Math.min(groups.length - 1, (s ?? -1) + 1)), [groups.length]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); goNext(); }
      if (e.key === "ArrowUp"   || e.key === "ArrowLeft")  { e.preventDefault(); goPrev(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goPrev, goNext]);

  if (error) return (
    <div className="p-10 text-bad">
      <strong>Failed to load data.json:</strong> {error}
      <div className="mt-2 text-muted-foreground text-xs">
        Run <code className="font-mono bg-muted px-1 py-0.5 rounded">python viewer/bundle_data.py</code> from the repo root first.
      </div>
    </div>
  );

  if (!data) return <Skeleton />;

  const selectedGroup = selected !== null ? groups[selected] : null;

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <Header
        config={data.config}
        run={data.run}
        selected={selected}
        total={groups.length}
        onPrev={goPrev}
        onNext={goNext}
      />
      <Narrative config={data.config} groups={groups} />
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <div className="w-72 flex-shrink-0 border-r border-sidebar-border flex flex-col overflow-hidden bg-sidebar">
          <Scoreboard groups={groups} backends={backends} />
          <div className="flex-1 overflow-y-auto" role="listbox" aria-label="Questions">
            {groups.map((g, i) => (
              <CaseRow
                key={g.case_id}
                group={g}
                backends={backends}
                selected={selected === i}
                index={i}
                onClick={() => setSelected(i)}
              />
            ))}
          </div>
          <div className="px-3 py-2 border-t border-sidebar-border text-[10px] text-muted-foreground text-center select-none">
            ← → keys or click to navigate
          </div>
        </div>

        {/* Detail pane */}
        <div className="flex-1 overflow-hidden bg-background">
          {selectedGroup
            ? <CaseDetail key={selectedGroup.case_id} group={selectedGroup} />
            : <WelcomePane groups={groups} backends={backends} onSelect={setSelected} />}
        </div>
      </div>
    </div>
  );
}

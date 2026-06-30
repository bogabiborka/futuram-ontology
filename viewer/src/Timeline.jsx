import { useState } from "react";
import { cn } from "./utils.js";

// Mirrors bench observer's Legend.js SIGNS + StepPill
const STEP = {
  query:    { cls: "text-blue border-blue/40 bg-blue/5",   icon: "⬡", word: "query" },
  search:   { cls: "text-ok border-ok/40 bg-ok/10",        icon: "⌕", word: "search" },
  skills:   { cls: "text-warn border-warn/40 bg-warn/5",   icon: "☰", word: "skills" },
  skill:    { cls: "text-warn border-warn/40 bg-warn/5",   icon: "▤", word: "skill" },
  answer:   { cls: "text-blue border-blue/50 bg-blue/10",  icon: "⚑", word: "answer" },
  reprompt: { cls: "text-bad border-bad/40 bg-bad/5",      icon: "↳", word: "retry" },
};

const KIND_SUFFIX = { data: "✓", empty: "∅", invalid: "✗" };
const KIND_CLS    = { data: "text-ok", empty: "text-muted-foreground", invalid: "text-bad" };

function Pill({ step, n }) {
  const [open, setOpen] = useState(false);
  const s = STEP[step.type] || STEP.query;
  const hasPopover = step.query || step.result || step.text || step.reason;

  const pill = (
    <span
      onClick={hasPopover ? () => setOpen((o) => !o) : undefined}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[11px] leading-none select-none",
        s.cls,
        hasPopover && "cursor-pointer"
      )}
      title={hasPopover ? "click to inspect" : undefined}
    >
      {n != null && <span className="opacity-50">{n}</span>}
      <span>{s.icon}</span>
      <span>{step.skill || s.word}</span>
      {step.kind && (
        <span className={cn("font-bold", KIND_CLS[step.kind])}>{KIND_SUFFIX[step.kind]}</span>
      )}
    </span>
  );

  if (!hasPopover) return pill;
  return (
    <span className="relative inline-block">
      {pill}
      {open && (
        <div className="absolute z-50 top-[calc(100%+4px)] left-0 bg-popover border border-border rounded-md shadow-lg min-w-[300px] max-w-[560px] max-h-[60vh] overflow-auto">
          <button
            onClick={() => setOpen(false)}
            className="absolute top-1.5 right-2 text-muted-foreground hover:text-foreground text-base leading-none bg-transparent border-none cursor-pointer"
          >×</button>
          {step.reason && (
            <div className="p-3 text-xs text-bad border-b border-border">
              <div className="font-semibold mb-1">Retry reason</div>
              {step.reason}
            </div>
          )}
          {step.query && (
            <>
              <div className="px-2.5 pt-2 pb-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground border-b border-border/60">SPARQL query</div>
              <pre className="md-raw m-0 rounded-none border-0 text-[11px]">{step.query}</pre>
            </>
          )}
          {(step.result || step.text) && (
            <>
              <div className="px-2.5 pt-2 pb-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground border-t border-border/60">
                {step.type === "answer" ? "answer handed in" : "result returned"}
              </div>
              <pre className="md-raw m-0 rounded-none border-0 text-[11px]">{step.result || step.text}</pre>
            </>
          )}
        </div>
      )}
    </span>
  );
}

export function Timeline({ timeline }) {
  if (!timeline?.length) return null;
  let queryN = 0;
  return (
    <div>
      <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground mb-1.5">
        steps
        <span className="normal-case tracking-normal font-normal ml-2">— each pill is one tool call; click to inspect query + result</span>
      </div>
      <div className="flex flex-col gap-1.5">
        {timeline.map((attempt, ai) => (
          <div key={ai}>
            {timeline.length > 1 && (
              <div className="text-[10px] text-muted-foreground mb-1">attempt {attempt.attempt}</div>
            )}
            <div className="flex flex-wrap gap-1 items-center">
              {attempt.steps.map((step, si) => {
                const n = step.type === "query" ? ++queryN : null;
                return <Pill key={si} step={step} n={n} />;
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

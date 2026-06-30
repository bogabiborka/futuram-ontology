"use client";
import { cn } from "@/lib/utils";
import { Progress } from "@/components/ui/progress";
import { BACKENDS, BE_COLOR, fmtTokens, fmtSecs } from "./format.js";

export function Scoreboard({ fq, composition }) {
  const pct = (t) => (t.n ? Math.round((100 * t.ok) / t.n) : 0);
  return (
    <div className="grid grid-cols-2 divide-x divide-border">
      {[["fq", fq], ["composition", composition]].map(([be, t]) => (
        <div key={be} className="px-3 first:pl-0 space-y-1">
          <div className={cn("font-mono text-xs uppercase tracking-wider", BE_COLOR[be])}>{be}</div>
          <div className="font-serif text-2xl leading-none">
            {t.ok}<span className="text-muted-foreground text-base">/{t.n}</span>
          </div>
          <Progress value={pct(t)} className="h-1.5" />
          <div className="font-mono text-[11px] text-muted-foreground">{fmtTokens(t.tok)} tok</div>
        </div>
      ))}
    </div>
  );
}

export function CaseRow({ cid, runs, maxTok, selected, onClick, n }) {
  return (
    <button onClick={onClick}
      className={cn(
        "w-full text-left rounded-md border px-3 py-2.5 transition-colors",
        selected ? "border-primary bg-accent" : "border-border bg-card hover:bg-accent/50"
      )}>
      <div className="flex items-baseline gap-2 mb-2">
        {n != null && (
          <span className="font-mono text-[10px] tabular-nums text-muted-foreground shrink-0">
            #{n}
          </span>
        )}
        <span className="font-mono text-xs font-medium break-all">{cid}</span>
      </div>
      <div className="space-y-1.5">
        {BACKENDS.map((be) => {
          const r = runs[be];
          if (!r) return (
            <div key={be} className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
              <span className="w-3 text-center">—</span>
              <span className={cn("w-24", BE_COLOR[be])}>{be}</span>
            </div>
          );
          // ask-mode (answered / no-answer) has NO verdict — use a neutral dot,
          // never the ✓/✗ pass-fail marks.
          const mark = r.status === "correct" ? "✓" : r.status === "wrong" ? "✗"
            : r.status === "answered" ? "•" : r.status === "no-answer" ? "○" : "…";
          const markCls = r.status === "correct" ? "text-ok" : r.status === "wrong" ? "text-bad" : "text-muted-foreground";
          return (
            <div key={be} className="flex items-center gap-2 font-mono text-[11px]"
              title={`${r.attempts}att · ${fmtTokens(r.tokens)} · ${r.tool_calls}q · ${fmtSecs(r.seconds)}`}>
              <span className={cn("w-3 text-center font-semibold", markCls)}>{mark}</span>
              <span className={cn("w-20", BE_COLOR[be])}>{be}</span>
              <span className="w-10 text-right text-muted-foreground">{fmtTokens(r.tokens)}</span>
              <span className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
                <span className="block h-full bg-foreground/40"
                  style={{ width: `${(100 * (r.tokens || 0)) / maxTok}%` }} />
              </span>
            </div>
          );
        })}
      </div>
    </button>
  );
}

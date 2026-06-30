"use client";
import { cn } from "@/lib/utils";
import {
  Search, ListChecks, BookOpen, CheckCircle2, MinusCircle, XCircle, HelpCircle,
  Crosshair, Calculator, Sigma, Ban, Unplug, Shapes, CloudOff, Flag, Timer, Gauge, Route,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Popover, PopoverContent, PopoverTrigger,
} from "@/components/ui/popover";
import {
  HoverCard, HoverCardContent, HoverCardTrigger,
} from "@/components/ui/hover-card";

// Canonical timeline-sign vocabulary — the single source of truth shared by the
// query timeline (CaseDetail) and this legend, so the icons can never drift.
// Each sign has a DISTINCT icon, a DISTINCT colour, AND a word label, so they
// never read as "all the same".
export const SIGNS = [
  { key: "data",    icon: CheckCircle2, word: "data",   label: "query returned rows",     cls: "text-ok border-ok/40 bg-ok/10" },
  { key: "empty",   icon: MinusCircle,  word: "empty",  label: "query returned no rows",  cls: "text-muted-foreground border-border bg-muted/40" },
  { key: "invalid", icon: XCircle,      word: "error",  label: "query invalid / errored", cls: "text-bad border-bad/40 bg-bad/10" },
  { key: "search",  icon: Search,       word: "search", label: "searched the docs",       cls: "text-blue border-blue/40 bg-blue/5" },
  { key: "skills",  icon: ListChecks,   word: "skills", label: "listed skills",           cls: "text-warn border-warn/40 bg-warn/5" },
  { key: "skill",   icon: BookOpen,     word: "skill",  label: "read a skill",            cls: "text-warn border-warn/40 bg-warn/5" },
  { key: "answer",  icon: Flag,         word: "answer", label: "the answer the model HANDED IN this attempt — hover to read its final message (the ANSWER line + reasoning)", cls: "text-blue border-blue/50 bg-blue/10" },
];

const signOf = (kind) => SIGNS.find((s) => s.key === kind) || SIGNS.find((s) => s.key === "empty");

// Canonical ERROR-CATEGORY vocabulary — one DISTINCT icon + colour + label per
// triaged failure kind (mirrors benchlib/scoring.py ERROR_CATEGORIES). The single
// source of truth shared by the per-run badge (CaseDetail) and the legend, so the
// icons never drift. "wrong-class" = resolved the wrong subject/class.
export const ERROR_SIGNS = [
  { key: "wrong-class",       icon: Crosshair,  label: "resolved the WRONG class/subject (e.g. a broad roll-up instead of the specific one) — the most common, most important error" },
  { key: "wrong-value",       icon: Calculator, label: "right class, wrong number (aggregation/arithmetic)" },
  { key: "wrong-uncertainty", icon: Sigma,      label: "value ok, the ± uncertainty is off" },
  { key: "wrong-route",       icon: Route,      label: "value+uncertainty ok, but the recovery process IRI is wrong" },
  { key: "no-answer",         icon: Ban,        label: "no parseable / numeric answer produced" },
  { key: "not-grounded",      icon: Unplug,     label: "answered without running a SPARQL query" },
  { key: "wrong-shape",       icon: Shapes,     label: "answer shape can't be scored (e.g. scalar value on a labelled case)" },
  { key: "provider-error",    icon: CloudOff,   label: "the LLM provider failed the call (rate limit / 5xx / auth) — NOT a model mistake" },
  { key: "timeout",           icon: Timer,      label: "wall-clock deadline exceeded — the model was still querying when time ran out" },
  { key: "token-cap",         icon: Gauge,      label: "token budget exhausted before an answer was produced" },
];

const errorSignOf = (cat) => ERROR_SIGNS.find((s) => s.key === cat);

// The error-category badge: a DISTINCT icon + the category word, red-outlined.
// Shown on a failing run so the KIND of error reads at a glance. `retries` (the
// wrong-subject re-prompt count) is appended when present.
export function ErrorBadge({ category, retries }) {
  const sign = errorSignOf(category);
  if (!sign) return null;
  const Icon = sign.icon;
  return (
    <span className="inline-flex items-center gap-1 rounded-sm border border-bad/50 bg-bad/5 px-1.5 py-0.5 font-mono text-[11px] leading-none text-bad"
          title={sign.label}>
      <Icon className="size-3" />{category}
      {retries ? <span className="opacity-60">·{retries} re-prompt</span> : null}
    </span>
  );
}

// A labelled step pill: icon + word, coloured by kind. Hovering shows the FULL
// SPARQL (when `query` is given) AND the RESULT that was handed back for this step
// (`result`) — so you can read both the query and exactly what it returned.
export function StepPill({ kind, n, query, sub, result }) {
  const sign = signOf(kind);
  const Icon = sign.icon;
  const pill = (
    <span className={cn(
      "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[11px] leading-none",
      sign.cls
    )}>
      {n != null && <span className="opacity-50">{n}</span>}
      <Icon className="size-3" />
      <span>{sub || sign.word}</span>
    </span>
  );
  const res = (result || "").trim();
  if (!query && !res) return pill;
  return (
    <HoverCard openDelay={80} closeDelay={40}>
      <HoverCardTrigger asChild><span className="cursor-help">{pill}</span></HoverCardTrigger>
      <HoverCardContent align="start" className="w-[42rem] max-w-[80vw] p-0">
        <div className="max-h-[70vh] overflow-auto">
          {query && (
            <>
              <div className="px-2.5 pt-2 pb-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">query</div>
              <pre className="md-raw m-0 rounded-none border-0 text-[11px]">{query}</pre>
            </>
          )}
          {res && (
            <>
              <div className="px-2.5 pt-2 pb-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground border-t border-border/60">
                {kind === "answer" ? "answer handed in (final message)" : "result handed back"}
              </div>
              <pre className="md-raw m-0 rounded-none border-0 text-[11px] whitespace-pre-wrap">{res}</pre>
            </>
          )}
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}

export function Legend({ trigger }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        {trigger || (
          <Button variant="ghost" size="sm" className="h-7 gap-1.5 font-mono text-xs text-muted-foreground">
            <HelpCircle className="size-3.5" /> what do the signs mean?
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <div className="space-y-2.5">
          <div className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
            query-timeline steps
          </div>
          <p className="text-xs text-muted-foreground leading-snug">
            One pill per step the model took, left → right. Hover a query step to
            read its full SPARQL.
          </p>
          <ul className="space-y-1.5">
            {SIGNS.map((s) => {
              const Icon = s.icon;
              return (
                <li key={s.key} className="flex items-center gap-2.5">
                  <span className={cn(
                    "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[11px] leading-none",
                    s.cls
                  )}>
                    <Icon className="size-3" />{s.word}
                  </span>
                  <span className="text-xs">{s.label}</span>
                </li>
              );
            })}
          </ul>

          <div className="font-mono text-xs uppercase tracking-wider text-muted-foreground pt-1">
            failure kind
          </div>
          <p className="text-xs text-muted-foreground leading-snug">
            On a failing run, a red badge shows WHAT KIND of error it was.
          </p>
          <ul className="space-y-1.5">
            {ERROR_SIGNS.map((s) => {
              const Icon = s.icon;
              return (
                <li key={s.key} className="flex items-start gap-2.5">
                  <span className="inline-flex items-center gap-1 rounded-sm border border-bad/50 bg-bad/5 px-1.5 py-0.5 font-mono text-[11px] leading-none text-bad whitespace-nowrap">
                    <Icon className="size-3" />{s.key}
                  </span>
                  <span className="text-xs">{s.label}</span>
                </li>
              );
            })}
          </ul>
        </div>
      </PopoverContent>
    </Popover>
  );
}

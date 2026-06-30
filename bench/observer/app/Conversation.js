"use client";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Collapsible, CollapsibleContent, CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronRight, ChevronDown } from "lucide-react";
import Markdown from "./Markdown.js";
import { classifyTool, shortArgs } from "./format.js";

function Turn({ m }) {
  const role = m.role;
  const calls = role === "assistant" ? (m.tool_calls || []) : [];
  const text = (m.content || "").trim();
  const c = m.content || "";
  const flags = [];
  if (role === "tool") {
    const kind = classifyTool(c);
    if (c.includes("[helper]")) flags.push(["helper", "secondary"]);
    if (c.includes("[next]")) flags.push(["next", "outline"]);
    if (c.includes("[diagnose]")) flags.push(["diagnose", "outline"]);
    if (kind) flags.push([kind, kind === "data" ? "default" : kind === "invalid" ? "destructive" : "secondary"]);
  }
  // hooks must run unconditionally — compute the early-return AFTER them
  const isBigData = role === "tool" && classifyTool(c) === "data" && c.length > 600;
  const [open, setOpen] = useState(!isBigData);
  if (role === "assistant" && !text && !calls.length) return null;
  if (role === "attempt") {
    return (
      <div className="mt-3 mb-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground border-t border-border/60 pt-2">
        — {m.content} —
      </div>
    );
  }

  let preview = "";
  if (role === "assistant") preview = calls.length ? calls.map((x) => `→ ${x.name}(${shortArgs(x.arguments)})`).join("  ") : text.replace(/\s+/g, " ").slice(0, 90);
  else preview = c.replace(/\s+/g, " ").slice(0, 90);
  const roleLabel = role === "assistant" ? `assistant${m.thinking_seconds != null ? ` · ${m.thinking_seconds}s` : ""}` : role === "tool" ? `tool · ${m.name}` : role;

  const roleColor =
    role === "assistant" ? "text-foreground"
    : role === "tool" ? "text-blue"
    : role === "user" ? "text-warn"
    : "text-muted-foreground";

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-md border bg-card">
      <CollapsibleTrigger asChild>
        <button className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left">
          {open ? <ChevronDown className="size-3.5 text-muted-foreground shrink-0" /> : <ChevronRight className="size-3.5 text-muted-foreground shrink-0" />}
          <span className={cn("font-mono text-[11px] font-medium shrink-0", roleColor)}>{roleLabel}</span>
          {flags.map(([lbl, variant], k) => (
            <Badge key={k} variant={variant} className="rounded-sm font-mono text-[10px] px-1.5 py-0">{lbl}</Badge>
          ))}
          {!open && <span className="font-mono text-[11px] text-muted-foreground truncate">{preview}</span>}
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="px-3 pb-2.5 pt-0.5 border-t">
          {role === "assistant" && text && <Markdown text={text} />}
          {role === "assistant" && calls.map((x, j) => (
            <div key={j} className="font-mono text-xs text-blue mt-1">→ {x.name}({shortArgs(x.arguments)})</div>
          ))}
          {role === "tool" && m.args?.sparql_query && (
            <pre className="md-raw">{m.args.sparql_query}</pre>
          )}
          {role === "tool" && <Markdown text={c} />}
          {(role === "user" || role === "system") && <Markdown text={c} />}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function Conversation({ attempts }) {
  const [open, setOpen] = useState(false);
  // Flatten all attempts into a single turn list, inserting an attempt marker
  // before each when there are multiple attempts.
  const msgs = (attempts || []).flatMap((a, ai) => {
    const turns = a?.messages || [];
    const marker = attempts.length > 1
      ? [{ role: "attempt", content: `attempt ${a?.attempt ?? ai + 1}` }] : [];
    return [...marker, ...turns];
  });
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="w-full justify-start font-mono text-xs text-muted-foreground h-7 px-2">
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          {open ? "hide conversation" : `show full conversation (${msgs.length} turns)`}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <Separator className="my-2" />
        <div className="space-y-1">
          {msgs.map((m, i) => <Turn key={i} m={m} />)}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

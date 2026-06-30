import { useState } from "react";
import { cn } from "./utils.js";
import { ChevronRight, ChevronDown } from "lucide-react";

function classifyTool(content) {
  const c = content || "";
  if (c.includes('"bindings"') || c.startsWith("Results of SPARQL")) return "data";
  if (c.includes("returned no results")) return "empty";
  if (c.includes("not valid") || c.includes("returned error")) return "invalid";
  return null;
}

function shortArgs(a) {
  if (!a) return "";
  if (a.sparql_query) return "sparql…";
  if (a.skill_id) return a.skill_id;
  if (a.question) return `"${(a.question || "").slice(0, 40)}…"`;
  return Object.keys(a).join(", ");
}

const KIND_CLS = {
  data:    "border-ok/40 bg-ok/10 text-ok",
  empty:   "border-border bg-muted text-muted-foreground",
  invalid: "border-bad/40 bg-bad/10 text-bad",
};

function Flag({ label, kind }) {
  return (
    <span className={cn(
      "inline-flex items-center rounded-sm border px-1.5 py-0 font-mono text-[10px] leading-snug",
      KIND_CLS[kind] || "border-border bg-muted text-muted-foreground"
    )}>
      {label}
    </span>
  );
}

// Safe markdown-ish renderer: code blocks, inline code, paragraphs.
// Does NOT use dangerouslySetInnerHTML — all content rendered as React nodes.
function InlineText({ text }) {
  // Split on `code` spans
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("`") && p.endsWith("`") && p.length > 2
          ? <code key={i} className="font-mono text-[0.85em] bg-muted px-[3px] py-[1px] rounded-sm">{p.slice(1, -1)}</code>
          : <span key={i}>{p}</span>
      )}
    </>
  );
}

function MdText({ text }) {
  if (!text) return null;
  const parts = text.split(/(```[\s\S]*?```)/g);
  return (
    <div className="text-sm leading-relaxed space-y-1">
      {parts.map((part, i) => {
        if (part.startsWith("```")) {
          const body = part.replace(/^```[^\n]*\n?/, "").replace(/```$/, "");
          return <pre key={i} className="md-raw text-[11px] mt-1">{body}</pre>;
        }
        return part.split("\n\n").map((para, j) =>
          para.trim() ? (
            <p key={`${i}-${j}`} className="my-0">
              {para.split("\n").map((line, k) => (
                <span key={k}>{k > 0 && <br />}<InlineText text={line} /></span>
              ))}
            </p>
          ) : null
        );
      })}
    </div>
  );
}

function Turn({ m }) {
  const role = m.role;
  const calls = role === "assistant" ? (m.tool_calls || []) : [];
  const text = (m.content || "").trim();
  const c = m.content || "";

  // Attempt separator marker (synthetic)
  if (role === "attempt") {
    return (
      <div className="mt-2 mb-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground border-t border-border/60 pt-1.5">
        — {m.content} —
      </div>
    );
  }

  const toolKind = role === "tool" ? classifyTool(c) : null;
  const flags = [];
  if (role === "tool") {
    if (c.includes("[helper]")) flags.push({ label: "helper", kind: "empty" });
    if (c.includes("[next]")) flags.push({ label: "next", kind: "empty" });
    if (c.includes("[diagnose]")) flags.push({ label: "diagnose", kind: "empty" });
    if (toolKind) flags.push({ label: toolKind, kind: toolKind });
  }

  // Big tool results start collapsed
  const isBigData = role === "tool" && toolKind === "data" && c.length > 600;
  const [open, setOpen] = useState(!isBigData);

  if (role === "assistant" && !text && !calls.length) return null;

  const preview = role === "assistant"
    ? (calls.length ? calls.map((x) => `→ ${x.name}(${shortArgs(x.arguments)})`).join("  ") : text.replace(/\s+/g, " ").slice(0, 100))
    : c.replace(/\s+/g, " ").slice(0, 100);

  const roleLabel =
    role === "assistant" ? `assistant${m.thinking_seconds != null ? ` · ${m.thinking_seconds}s` : ""}`
    : role === "tool" ? `tool · ${m.name || ""}`
    : role;

  const roleColor =
    role === "assistant" ? "text-foreground"
    : role === "tool" ? "text-blue"
    : role === "user" ? "text-warn"
    : "text-muted-foreground";

  return (
    <div className="rounded-md border border-border bg-card overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-accent/40 transition-colors bg-transparent"
      >
        {open
          ? <ChevronDown className="size-3.5 text-muted-foreground shrink-0" />
          : <ChevronRight className="size-3.5 text-muted-foreground shrink-0" />}
        <span className={cn("font-mono text-[11px] font-medium shrink-0", roleColor)}>{roleLabel}</span>
        {flags.map((f, k) => <Flag key={k} label={f.label} kind={f.kind} />)}
        {!open && <span className="font-mono text-[11px] text-muted-foreground truncate">{preview}</span>}
      </button>
      {open && (
        <div className="px-3 pb-2.5 pt-1 border-t border-border/60">
          {role === "assistant" && text && <MdText text={text} />}
          {role === "assistant" && calls.map((x, j) => (
            <div key={j} className="font-mono text-xs text-blue mt-1">
              → {x.name}({shortArgs(x.arguments)})
            </div>
          ))}
          {role === "tool" && <MdText text={c} />}
          {(role === "user" || role === "system") && <MdText text={c} />}
        </div>
      )}
    </div>
  );
}

export function Conversation({ conversation }) {
  const [open, setOpen] = useState(false);

  const msgs = (conversation || []).flatMap((a, ai) => {
    const turns = a?.messages || [];
    const marker = conversation.length > 1
      ? [{ role: "attempt", content: `attempt ${a?.attempt ?? ai + 1}` }]
      : [];
    return [...marker, ...turns];
  });

  const total = msgs.filter((m) => m.role !== "attempt").length;

  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 px-0 py-1 font-mono text-xs text-muted-foreground hover:text-foreground bg-transparent border-none cursor-pointer w-full text-left"
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        {open ? "hide conversation" : `show full conversation (${total} turns)`}
      </button>
      {open && (
        <div className="mt-2 space-y-1">
          {msgs.map((m, i) => <Turn key={i} m={m} />)}
        </div>
      )}
    </div>
  );
}

"use client";
import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Play, Loader2 } from "lucide-react";
import { InlineExpectedEditor } from "./Answer.js";
import { CopilotQuotaChip } from "./ModelPicker.js";

// One combined panel: pick the LLM (BYOK/BYOM provider profile + optional model
// override), ask a question AND (optionally) set its expected answer, then run
// BOTH backends together. The expected travels with the run as a transcript —
// nothing is written into the canonical library — so setting an expected can
// never leave behind an orphan case. The API KEY is never entered or sent here:
// a non-Ollama provider uses the key the SERVER holds in the env var its
// providers.json entry names.
export function NewPanel({ onAsked }) {
  const [question, setQuestion] = useState("");
  const [expected, setExpected] = useState(null); // {unit,values} | {names:[]} | null
  const [asking, setAsking] = useState(false);
  const [msg, setMsg] = useState("");
  const [providers, setProviders] = useState([]);   // [{name,provider,model,key_env,key_set}]
  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("");           // optional override

  useEffect(() => {
    fetch("/api/providers")
      .then((r) => r.json())
      .then((j) => setProviders(j.profiles || []))
      .catch(() => setProviders([]));
  }, []);

  const sel = providers.find((p) => p.name === provider);
  const keyMissing = sel && sel.key_env && !sel.key_set;

  const run = async () => {
    const q = question.trim();
    if (!q) return;
    setAsking(true); setMsg("");
    try {
      const r = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          question: q,
          ...(provider && provider !== "ollama" ? { provider } : {}),
          ...(model.trim() ? { model: model.trim() } : {}),
          ...(expected ? { expected } : {}),
        }),
      });
      const j = await r.json();
      setMsg(r.ok
        ? `started ${j.id} on ${j.provider}:${j.model}${expected ? " (scored)" : " (unscored)"}`
        : (j.error || "failed"));
      if (r.ok) { setQuestion(""); setExpected(null); onAsked && onAsked(); }
    } catch (e) { setMsg(String(e)); }
    finally { setAsking(false); }
  };

  return (
    <div className="space-y-3">
      {/* LLM picker — provider profile + optional model override (BYOK/BYOM) */}
      <div className="space-y-1.5">
        <div className="font-mono text-xs uppercase tracking-wider text-muted-foreground">model</div>
        <div className="flex gap-2">
          <select
            value={provider}
            onChange={(e) => { setProvider(e.target.value); setModel(""); }}
            className="flex h-8 w-1/2 rounded-md border border-input bg-transparent px-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            {providers.length === 0 && <option value="ollama">ollama</option>}
            {providers.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}{p.provider !== p.name ? ` · ${p.provider}` : ""}
              </option>
            ))}
          </select>
          <Input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={sel?.model || "default model"}
            className="h-8 w-1/2 font-mono text-xs"
          />
        </div>
        {keyMissing && (
          <div className="font-mono text-[10px] text-amber-600">
            ⚠ ${sel.key_env} is not set on the server — this run will fail to
            authenticate. Set the env var (or use --login) before running.
          </div>
        )}
        {/* live Copilot budget — answers "do I still have budget for gpt-4o?" */}
        <CopilotQuotaChip provider={sel?.provider || provider} />
      </div>

      <Separator />

      <div className="space-y-1.5">
        <div className="font-mono text-xs uppercase tracking-wider text-muted-foreground">question</div>
        <Textarea rows={3} placeholder="Ask in natural language — runs on fq AND composition…"
          value={question} onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run(); }}
          className="font-mono text-xs resize-none" />
      </div>

      <Separator />

      <div className="space-y-1.5">
        <div className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
          expected answer <span className="normal-case font-normal text-[10px]">— optional; set it to score the run</span>
        </div>
        <InlineExpectedEditor value={expected} onChange={setExpected} />
      </div>

      <Button size="sm" className="w-full font-mono text-xs" onClick={run} disabled={asking || !question.trim()}>
        {asking
          ? <><Loader2 className="size-3.5 animate-spin" /> starting…</>
          : <><Play className="size-3.5" /> {expected ? "Set expected & run both · ⌘↵" : "Run both · ⌘↵"}</>}
      </Button>
      {msg && <div className="font-mono text-xs text-muted-foreground">{msg}</div>}
    </div>
  );
}

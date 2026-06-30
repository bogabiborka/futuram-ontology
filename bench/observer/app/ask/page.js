"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Play, Loader2, Home, Plus, RotateCcw, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { ModelPicker, CopilotQuotaChip } from "../ModelPicker.js";
import { CaseDetail } from "../CaseDetail.js";

// Self-contained ASK page — REMEMBERED + RESTARTABLE. The left rail is the history
// of every ad-hoc ask (newest first); the right pane shows the selected ask's
// answer (CaseDetail in ASK MODE: answer shown, no pass/fail verdict, no expected).
// Each ask runs on BOTH backends, lives in its own folder bench/asks/<id>/, and can
// be re-run. Fully separate from the experiment run browser.
const POLL_MS = 2000;

export default function AskPage() {
  const [asks, setAsks] = useState([]);          // history (meta list, newest first)
  const [running, setRunning] = useState([]);    // ids currently running
  const [sel, setSel] = useState(null);          // selected ask id (or "new")
  const [runs, setRuns] = useState({});           // selected ask: { backend: summary }
  const [details, setDetails] = useState({});     // { runId: full }
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  // new-ask form
  const [question, setQuestion] = useState("");
  const [providers, setProviders] = useState([]);
  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("");
  const [timeout_s, setTimeout_s] = useState(900);     // wall-clock limit (seconds)
  const [tokenBudget, setTokenBudget] = useState(400000); // token limit
  const textareaRef = useRef(null);

  useEffect(() => {
    fetch("/api/providers").then((r) => r.json()).then((j) => setProviders(j.profiles || [])).catch(() => {});
  }, []);

  // poll the history (cheap) so new/running asks appear and statuses update.
  const loadHistory = useCallback(async () => {
    try {
      const j = await (await fetch("/api/ask", { cache: "no-store" })).json();
      setAsks(j.asks || []);
      setRunning(j.running || []);
    } catch {}
  }, []);
  useEffect(() => { loadHistory(); const t = setInterval(loadHistory, POLL_MS); return () => clearInterval(t); }, [loadHistory]);

  // poll the SELECTED ask's transcripts (its own folder)
  const selDir = sel && sel !== "new" ? `bench/asks/${sel}` : null;
  const pollSel = useCallback(async () => {
    if (!selDir) { setRuns({}); setDetails({}); return; }
    const qs = `?dir=${encodeURIComponent(selDir)}`;
    try {
      const j = await (await fetch(`/api/runs${qs}`, { cache: "no-store" })).json();
      const list = (j.runs || []).filter((r) => String(r.case_id || "").startsWith("ask_"));
      const byBackend = {};
      for (const r of list) byBackend[r.backend] = r;
      setRuns(byBackend);
      for (const r of list) {
        try {
          const d = await (await fetch(`/api/run/${r.id}${qs}`, { cache: "no-store" })).json();
          if (d && !d.error) setDetails((p) => ({ ...p, [r.id]: d }));
        } catch {}
      }
    } catch {}
  }, [selDir]);
  useEffect(() => { setDetails({}); pollSel(); const t = setInterval(pollSel, POLL_MS); return () => clearInterval(t); }, [pollSel]);

  const sel_meta = asks.find((a) => a.id === sel);
  const sel_provider = providers.find((p) => p.name === provider);
  const keyMissing = sel_provider && sel_provider.key_env && !sel_provider.key_set;

  const submit = async () => {
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true); setMsg("");
    try {
      const r = await fetch("/api/ask", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: q, backends: "fq,composition",
          ...(provider && provider !== "ollama" ? { provider } : {}),
          ...(model.trim() ? { model: model.trim() } : {}),
          timeout: Number(timeout_s) || 900,
          tokenBudget: Number(tokenBudget) || 400000 }),
      });
      const j = await r.json();
      if (r.ok) { setQuestion(""); setSel(j.id); loadHistory(); }
      else setMsg(j.error || "failed to start");
    } catch (e) { setMsg(String(e)); } finally { setBusy(false); }
  };

  const rerun = async (id) => {
    setMsg("");
    try {
      const r = await fetch(`/api/ask?rerun=${encodeURIComponent(id)}`, { method: "POST" });
      const j = await r.json();
      if (r.ok) { setSel(id); loadHistory(); } else setMsg(j.error || "re-run failed");
    } catch (e) { setMsg(String(e)); }
  };

  const del = async (id) => {
    if (!confirm("Delete this ask and its transcripts?")) return;
    try {
      await fetch(`/api/ask?id=${encodeURIComponent(id)}`, { method: "DELETE" });
      if (sel === id) setSel(null);
      loadHistory();
    } catch (e) { setMsg(String(e)); }
  };

  const showForm = sel === "new" || (!sel && asks.length === 0);
  const isRunning = (id) => running.includes(id);

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-background">
      <header className="flex items-center gap-3 px-5 h-12 border-b bg-sidebar shrink-0">
        <Button asChild variant="ghost" size="sm" className="h-7 gap-1.5 font-mono text-xs text-muted-foreground">
          <Link href="/"><Home className="size-3.5" /> home</Link>
        </Button>
        <span className="font-mono text-xs text-muted-foreground">/</span>
        <Button asChild variant="ghost" size="sm" className="h-7 gap-1.5 font-mono text-xs text-muted-foreground">
          <Link href="/runs">experiment runs</Link>
        </Button>
        <h1 className="ml-2 font-serif text-base font-semibold">Ask</h1>
        <span className="font-mono text-[11px] text-muted-foreground">ad-hoc questions · both backends · remembered</span>
      </header>

      <div className="grid flex-1 overflow-hidden min-h-0" style={{ gridTemplateColumns: "300px 1fr" }}>
        {/* HISTORY rail */}
        <aside className="flex flex-col border-r bg-sidebar overflow-hidden min-h-0">
          <div className="p-3 border-b">
            <Button size="sm" className="w-full gap-1.5 font-mono text-xs" onClick={() => { setSel("new"); setTimeout(() => textareaRef.current?.focus(), 0); }}>
              <Plus className="size-3.5" /> new ask
            </Button>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {asks.length === 0 && (
              <div className="px-2 py-6 text-center font-mono text-[11px] text-muted-foreground">No asks yet.</div>
            )}
            {asks.map((a) => (
              <button key={a.id} onClick={() => setSel(a.id)}
                className={cn("w-full text-left rounded-md px-2.5 py-2 border transition-colors",
                  sel === a.id ? "border-foreground/30 bg-accent" : "border-transparent hover:bg-accent/50")}>
                <div className="flex items-center gap-1.5">
                  {isRunning(a.id) && <Loader2 className="size-3 animate-spin text-muted-foreground shrink-0" />}
                  <span className="font-mono text-[13px] leading-snug line-clamp-2">{a.question}</span>
                </div>
                <div className="mt-1 font-mono text-[10px] text-muted-foreground truncate">
                  {a.provider}:{a.model}
                </div>
              </button>
            ))}
          </div>
        </aside>

        {/* DETAIL / FORM pane */}
        <main className="overflow-y-auto">
          {showForm ? (
            <div className="flex items-center justify-center h-full px-4">
              <div className="w-full max-w-xl space-y-4">
                <h2 className="font-serif text-xl font-semibold">New ask</h2>
                <div className="space-y-1.5">
                  <div className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">model</div>
                  <div className="flex gap-2">
                    <select value={provider} onChange={(e) => { setProvider(e.target.value); setModel(""); }}
                      className="flex h-8 w-1/2 rounded-md border border-input bg-transparent px-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
                      {providers.length === 0 && <option value="ollama">ollama</option>}
                      {providers.map((p) => <option key={p.name} value={p.name}>{p.name}{p.provider !== p.name ? ` · ${p.provider}` : ""}</option>)}
                    </select>
                    <div className="w-1/2"><ModelPicker provider={provider} value={model} onChange={setModel} placeholder={sel_provider?.model || "default model"} /></div>
                  </div>
                  {keyMissing && <div className="font-mono text-[10px] text-amber-600">⚠ ${sel_provider.key_env} not set on server</div>}
                  <CopilotQuotaChip provider={sel_provider?.provider || provider} />
                </div>
                {/* limits — wall-clock time + token budget for the run */}
                <div className="flex gap-3">
                  <label className="flex-1 space-y-1">
                    <div className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">time limit (s)</div>
                    <input type="number" min={30} max={7200} step={30} value={timeout_s}
                      onChange={(e) => setTimeout_s(e.target.value)}
                      className="flex h-8 w-full rounded-md border border-input bg-transparent px-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring" />
                  </label>
                  <label className="flex-1 space-y-1">
                    <div className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">token limit</div>
                    <input type="number" min={1000} max={5000000} step={10000} value={tokenBudget}
                      onChange={(e) => setTokenBudget(e.target.value)}
                      className="flex h-8 w-full rounded-md border border-input bg-transparent px-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring" />
                  </label>
                </div>
                <Textarea ref={textareaRef} rows={5} placeholder="Ask a question about materials, elements, vehicles…"
                  value={question} onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }}
                  className="font-mono text-sm resize-none" />
                <div className="font-mono text-[10px] text-muted-foreground text-right">⌘↵ to run</div>
                <Button className="w-full font-mono text-sm" onClick={submit} disabled={busy || !question.trim()}>
                  {busy ? <><Loader2 className="size-4 animate-spin mr-2" /> starting…</> : <><Play className="size-4 mr-2" /> Ask</>}
                </Button>
                {msg && <div className="font-mono text-xs text-destructive">{msg}</div>}
              </div>
            </div>
          ) : sel_meta ? (
            <div className="w-full">
              {/* selected ask: actions + the answer (ask mode) */}
              <div className="flex items-center gap-2 px-6 pt-4">
                <Button size="sm" variant="outline" className="h-7 gap-1.5 font-mono text-xs"
                  onClick={() => rerun(sel)} disabled={isRunning(sel)}>
                  {isRunning(sel) ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCcw className="size-3.5" />} re-run
                </Button>
                <Button size="sm" variant="ghost" className="h-7 gap-1.5 font-mono text-xs text-destructive" onClick={() => del(sel)}>
                  <Trash2 className="size-3.5" /> delete
                </Button>
                {msg && <span className="font-mono text-xs text-destructive">{msg}</span>}
              </div>
              <CaseDetail cid={sel} runs={runs} details={details} onSaved={pollSel} hideExpected />
            </div>
          ) : (
            <div className="flex h-full items-center justify-center font-mono text-sm text-muted-foreground">
              Select an ask, or start a new one.
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

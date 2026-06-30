"use client";
import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Play, Square, RotateCcw, Loader2, FlaskConical, Plus, Save, Trash2, Pencil, KeyRound } from "lucide-react";
import { ModelPicker } from "./ModelPicker.js";

// Saved, editable experiments. Each is a named config (testcases, provider,
// model, backends, params) at bench/experiments/<name>/; running one archives its
// transcripts under that experiment's runs/<ts>/ folder and streams to
// /runs?dir=. Switching the model is one dropdown + a model field. No keys here.
const BLANK = {
  name: "", testcases: "bench/testcases/domain.yaml", provider: "ollama",
  model: "", backends: ["fq", "composition"], skills: true,
  maxSteps: 16, maxAttempts: 3, tokenBudget: 400000, timeout: 900, temperature: 0, note: "",
  // escalate: if set, a failed case on the listed backends is retried once with
  // this (better) model before being recorded as wrong.
  escalate: null,  // null = disabled; { provider, model, backends: [] } when on
};

export function Experiments({ providers }) {
  const [experiments, setExperiments] = useState([]);
  const [testcases, setTestcases] = useState([]);
  const [editing, setEditing] = useState(null); // config object being edited, or null
  const [status, setStatus] = useState({});      // {running, info, exitCode} (shared single runner)
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    try {
      const j = await (await fetch("/api/experiments", { cache: "no-store" })).json();
      setExperiments(j.experiments || []);
    } catch {}
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    fetch("/api/testcases").then((r) => r.json()).then((j) => setTestcases(j.testcases || [])).catch(() => {});
  }, []);

  // poll the shared experiment-runner status (which experiment is running)
  const pollStatus = useCallback(async () => {
    // status lives on the [name] runner; we discover the running one by asking
    // each experiment's GET would be heavy — instead the run POST returns info and
    // we track the running name locally. Simpler: poll the generic /api/run too.
    try {
      const r = await fetch("/api/run", { cache: "no-store" });
      setStatus(await r.json());
    } catch {}
  }, []);
  useEffect(() => { pollStatus(); const t = setInterval(pollStatus, 2500); return () => clearInterval(t); }, [pollStatus]);

  const save = async () => {
    if (!editing?.name?.trim()) { setMsg("name is required"); return; }
    setMsg("");
    try {
      const r = await fetch("/api/experiments", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(editing),
      });
      const j = await r.json();
      if (!r.ok) { setMsg(j.error || "save failed"); return; }
      setEditing(null); load();
    } catch (e) { setMsg(String(e)); }
  };

  const run = async (name) => {
    setMsg("");
    try {
      const r = await fetch(`/api/experiments/${name}?action=run`, { method: "POST" });
      const j = await r.json();
      setMsg(r.ok ? `running ${j.id}` : (j.error || "run failed"));
    } catch (e) { setMsg(String(e)); }
  };
  const cont = async (name) => {
    setMsg("");
    try {
      const r = await fetch(`/api/experiments/${name}?action=continue`, { method: "POST" });
      const j = await r.json();
      setMsg(r.ok ? `continuing ${j.id}` : (j.error || "continue failed"));
    } catch (e) { setMsg(String(e)); }
  };
  const stop = async (name) => {
    try { await fetch(`/api/experiments/${name}?action=stop`, { method: "POST" }); setMsg("stopping…"); }
    catch (e) { setMsg(String(e)); }
  };
  const del = async (name) => {
    if (!confirm(`Delete experiment "${name}" and all its runs?`)) return;
    try { await fetch(`/api/experiments/${name}`, { method: "DELETE" }); load(); }
    catch (e) { setMsg(String(e)); }
  };

  return (
    <Card className="p-5 space-y-4">
      <div className="flex items-center gap-2">
        <FlaskConical className="size-4" />
        <h3 className="font-serif text-lg font-semibold">Experiments</h3>
        <a href="#providers" className="ml-auto">
          <Button size="sm" variant="ghost" className="h-8 gap-1.5 font-mono text-xs" title="Connect a provider / add an API key">
            <KeyRound className="size-3.5" /> Add a key
          </Button>
        </a>
        <Button size="sm" variant="outline" className="h-8 gap-1.5 font-mono text-xs"
          onClick={() => setEditing({ ...BLANK })}>
          <Plus className="size-3.5" /> New
        </Button>
      </div>

      {experiments.length === 0 && !editing && (
        <div className="font-mono text-xs text-muted-foreground">No experiments yet — create one to save a reusable run config.</div>
      )}

      <div className="space-y-2">
        {experiments.map((e) => (
          <ExperimentRow key={e.name} e={e} status={status}
            onEdit={() => setEditing({ ...BLANK, ...e })}
            onRun={() => run(e.name)} onContinue={() => cont(e.name)}
            onStop={() => stop(e.name)} onDelete={() => del(e.name)} />
        ))}
      </div>

      {editing && (
        <Editor cfg={editing} setCfg={setEditing} providers={providers} testcases={testcases}
          onSave={save} onCancel={() => { setEditing(null); setMsg(""); }} />
      )}

      {msg && <div className="font-mono text-xs text-muted-foreground">{msg}</div>}
    </Card>
  );
}

function ExperimentRow({ e, status, onEdit, onRun, onContinue, onStop, onDelete }) {
  const running = status?.running && status.info?.experiment === e.name;
  const hasRuns = (e.runs ?? 0) > 0;
  return (
    <div className="flex items-center gap-3 rounded-md border px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="font-mono text-sm truncate">{e.name}</div>
        <div className="font-mono text-[11px] text-muted-foreground truncate">
          <span className="text-foreground/80">{e.provider}{e.model ? `:${e.model}` : ""}</span>
          {" · "}{e.testcases?.split("/").pop()}{" · "}{(e.backends || []).join("+")}
          {" · "}{e.runs ?? 0} run{e.runs === 1 ? "" : "s"}
        </div>
      </div>
      {running
        ? <Button size="sm" variant="destructive" className="h-7 gap-1.5 font-mono text-xs" onClick={onStop}><Square className="size-3.5" /> stop</Button>
        : <>
            {hasRuns && (
              <Button size="sm" variant="outline" className="h-7 gap-1.5 font-mono text-xs" onClick={onContinue}
                title="resume the latest run: skip done cases, re-run the interrupted/missing ones">
                <RotateCcw className="size-3.5" /> continue
              </Button>
            )}
            <Button size="sm" className="h-7 gap-1.5 font-mono text-xs" onClick={onRun}><Play className="size-3.5" /> run</Button>
          </>}
      <Link href={`/runs?dir=${encodeURIComponent(`bench/experiments/${e.name}/runs`)}`}
        className="font-mono text-[11px] text-muted-foreground underline underline-offset-2 hover:text-foreground">watch</Link>
      <Button size="icon" variant="ghost" className="size-7" onClick={onEdit} title="edit"><Pencil className="size-3.5" /></Button>
      <Button size="icon" variant="ghost" className="size-7 text-destructive" onClick={onDelete} title="delete"><Trash2 className="size-3.5" /></Button>
    </div>
  );
}

const selectCls = "flex h-8 w-full rounded-md border border-input bg-transparent px-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

// Providers the user can actually pick: those the server can authenticate
// (key_set), plus ollama (needs no key). The currently-selected provider is kept
// even if unkeyed, so an existing config still loads and shows "(no key)".
function usableProviders(providers, current) {
  const all = providers || [];
  // a provider is offered if it can run today (usable) or is the current pick;
  // fall back to ollama so the dropdown is never empty.
  const usable = all.filter((p) => p.usable || p.name === current);
  return usable.length ? usable : [{ name: "ollama", provider: "ollama", usable: false }];
}

function Editor({ cfg, setCfg, providers, testcases, onSave, onCancel }) {
  const set = (k, v) => setCfg((c) => ({ ...c, [k]: v }));
  const sel = (providers || []).find((p) => p.name === cfg.provider);
  const keyMissing = sel && sel.key_env && !sel.key_set;
  const toggleBackend = (b) => set("backends",
    cfg.backends.includes(b) ? cfg.backends.filter((x) => x !== b) : [...cfg.backends, b]);

  return (
    <div className="rounded-md border bg-muted/30 p-4 space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Field label="name"><Input value={cfg.name} onChange={(e) => set("name", e.target.value)} placeholder="my-experiment" className="h-8 font-mono text-xs" /></Field>
        <Field label="testcases">
          <select value={cfg.testcases} onChange={(e) => set("testcases", e.target.value)} className={selectCls}>
            {testcases.map((t) => <option key={t.path} value={t.path}>{t.file} · {t.cases}c</option>)}
          </select>
        </Field>
        <Field label="note (optional)"><Input value={cfg.note || ""} onChange={(e) => set("note", e.target.value)} className="h-8 font-mono text-xs" /></Field>
      </div>

      {/* MODEL SWITCH — provider dropdown + model field, front and centre */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="provider (model)">
          <select value={cfg.provider} onChange={(e) => { set("provider", e.target.value); set("model", ""); }} className={selectCls}>
            {/* only providers the server can authenticate (key set / token cached);
                ollama always shown — it needs no key. The current saved provider is
                kept even if its key just went missing, so the config still loads. */}
            {usableProviders(providers, cfg.provider).map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}{p.provider !== p.name ? ` · ${p.provider}` : ""}{p.key_set ? "" : " (no key)"}
              </option>
            ))}
          </select>
        </Field>
        <Field label="model (from provider — blank = profile default)">
          <ModelPicker provider={cfg.provider} value={cfg.model}
            onChange={(m) => set("model", m)} placeholder={sel?.model || "default"} />
        </Field>
      </div>
      {keyMissing && (
        <div className="font-mono text-[11px] text-amber-600">
          ⚠ ${sel.key_env} not set on the server — <a href="#providers" className="underline underline-offset-2">add a key</a> before running.
        </div>
      )}

      <div className="flex flex-wrap items-center gap-4">
        <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">backends</span>
        {["fq", "composition"].map((b) => (
          <label key={b} className="flex items-center gap-1.5 font-mono text-xs cursor-pointer">
            <input type="checkbox" checked={cfg.backends.includes(b)} onChange={() => toggleBackend(b)} /> {b}
          </label>
        ))}
        <label className="flex items-center gap-1.5 font-mono text-xs cursor-pointer ml-2">
          <input type="checkbox" checked={cfg.skills !== false} onChange={(e) => set("skills", e.target.checked)} /> skills
        </label>
      </div>

      {/* ESCALATION — on failure, retry once with a better model */}
      <EscalateEditor escalate={cfg.escalate} setEscalate={(v) => set("escalate", v)}
        providers={providers} backends={cfg.backends} />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Field label="max steps"><Input type="number" value={cfg.maxSteps} onChange={(e) => set("maxSteps", e.target.value)} className="h-8 font-mono text-xs" /></Field>
        <Field label="max attempts"><Input type="number" value={cfg.maxAttempts} onChange={(e) => set("maxAttempts", e.target.value)} className="h-8 font-mono text-xs" /></Field>
        <Field label="token budget"><Input type="number" value={cfg.tokenBudget} onChange={(e) => set("tokenBudget", e.target.value)} className="h-8 font-mono text-xs" /></Field>
        <Field label="timeout (s)"><Input type="number" value={cfg.timeout} onChange={(e) => set("timeout", e.target.value)} className="h-8 font-mono text-xs" /></Field>
        <Field label="temperature"><Input type="number" step="0.1" min="0" max="2" value={cfg.temperature} onChange={(e) => set("temperature", e.target.value)} className="h-8 font-mono text-xs" /></Field>
      </div>

      <Separator />
      <div className="flex items-center gap-2">
        <Button size="sm" className="gap-1.5 font-mono text-xs" onClick={onSave}><Save className="size-3.5" /> Save</Button>
        <Button size="sm" variant="ghost" className="font-mono text-xs" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

function EscalateEditor({ escalate, setEscalate, providers, backends }) {
  const enabled = escalate !== null && escalate !== undefined;
  const esc = escalate || { provider: "ollama", model: "", backends: [] };
  const sel = (providers || []).find((p) => p.name === esc.provider);

  const toggle = (on) => setEscalate(on ? { provider: "ollama", model: "", backends: [] } : null);
  const set = (k, v) => setEscalate({ ...esc, [k]: v });
  const toggleBe = (b) => set("backends",
    esc.backends.includes(b) ? esc.backends.filter((x) => x !== b) : [...esc.backends, b]);

  return (
    <div className="rounded-md border border-dashed px-3 py-2.5 space-y-2.5">
      <label className="flex items-center gap-2 font-mono text-xs cursor-pointer">
        <input type="checkbox" checked={enabled} onChange={(e) => toggle(e.target.checked)} />
        <span className="font-semibold">escalate on failure</span>
        <span className="text-muted-foreground">— retry failed cases once with a better model</span>
      </label>
      {enabled && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="escalate provider">
              <select value={esc.provider}
                onChange={(e) => { set("provider", e.target.value); set("model", ""); }}
                className={selectCls}>
                {usableProviders(providers, esc.provider).map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}{p.provider !== p.name ? ` · ${p.provider}` : ""}{p.key_set ? "" : " (no key)"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="escalate model">
              <ModelPicker provider={esc.provider} value={esc.model}
                onChange={(m) => set("model", m)} placeholder={sel?.model || "default"} />
            </Field>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">escalate on</span>
            {["fq", "composition"].map((b) => (
              <label key={b} className={`flex items-center gap-1.5 font-mono text-xs cursor-pointer ${!backends.includes(b) ? "opacity-40" : ""}`}
                title={!backends.includes(b) ? `${b} is not in the run's backends` : ""}>
                <input type="checkbox" checked={esc.backends.includes(b)}
                  disabled={!backends.includes(b)}
                  onChange={() => toggleBe(b)} /> {b}
              </label>
            ))}
            {esc.backends.length === 0 && (
              <span className="font-mono text-[11px] text-amber-600">⚠ no backends selected — escalation won't fire</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div className="space-y-1">
      <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}

"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Plus, Columns2, PanelLeft, PanelLeftClose, PanelLeftOpen, Home, Cpu, Play, Loader2, FlaskConical, Square, RotateCcw, ChevronDown } from "lucide-react";
import { useRouter } from "next/navigation";
import { Legend } from "./Legend.js";
import { ModelPicker, CopilotQuotaChip } from "./ModelPicker.js";
import { BE_COLOR, fmtTokens } from "./format.js";

// Shows the selected run's model + params, and (for an experiment view) lets you
// switch the model and re-run in place — no trip back to the homepage. The key is
// never touched here; switching only changes the provider/model the server uses.
function RunControls({ runMeta, experiment, onRelaunched, adhoc }) {
  const [providers, setProviders] = useState([]);
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    fetch("/api/providers").then((r) => r.json()).then((j) => setProviders(j.profiles || [])).catch(() => {});
  }, []);
  useEffect(() => {
    if (runMeta) { setProvider(runMeta.provider || ""); setModel(runMeta.model || ""); }
  }, [runMeta?.provider, runMeta?.model]);

  if (!runMeta) return null;
  const params = [
    runMeta.max_steps != null && `steps ${runMeta.max_steps}`,
    runMeta.max_attempts != null && `att ${runMeta.max_attempts}`,
    runMeta.token_budget != null && `budget ${fmtTokens(runMeta.token_budget)}`,
    runMeta.timeout != null && `${runMeta.timeout}s`,
  ].filter(Boolean).join(" · ");

  const relaunch = async () => {
    if (!experiment) { setMsg("this is an ad-hoc run — pick an experiment in the “run experiment” menu to launch a full suite"); return; }
    setBusy(true); setMsg("");
    try {
      // save the new provider/model onto the experiment, then re-run it
      const cur = await (await fetch(`/api/experiments/${experiment}`)).json();
      const r = await fetch("/api/experiments", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...cur, provider, model }),
      });
      if (!r.ok) { setMsg((await r.json()).error || "save failed"); setBusy(false); return; }
      const run = await (await fetch(`/api/experiments/${experiment}?action=run`, { method: "POST" })).json();
      setMsg(run.ok ? "re-running…" : (run.error || "run failed"));
      setOpen(false); onRelaunched && onRelaunched();
    } catch (e) { setMsg(String(e)); }
    finally { setBusy(false); }
  };

  const selectCls = "h-7 rounded-md border border-input bg-transparent px-1.5 font-mono text-[11px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";
  return (
    <div className="flex items-center gap-2">
      <button onClick={() => setOpen((v) => !v)} title="model & params — click to switch"
        className="flex items-center gap-1.5 rounded-sm border px-2 py-1 font-mono text-[11px] hover:bg-accent">
        <Cpu className="size-3.5" />
        <span className="font-semibold">{runMeta.provider}:{runMeta.model}</span>
        {params && <span className="hidden lg:inline text-muted-foreground">· {params}</span>}
      </button>
      {open && (
        <div className="absolute top-14 left-1/2 -translate-x-1/2 z-20 w-[28rem] rounded-md border bg-popover p-3 shadow-md space-y-2">
          <div className="font-mono text-[11px] text-muted-foreground">
            {experiment ? <>switch model for experiment <span className="text-foreground">{experiment}</span> and re-run</> : "ad-hoc run — switch model on the homepage editor"}
          </div>
          <div className="flex items-center gap-2">
            <select value={provider} onChange={(e) => { setProvider(e.target.value); setModel(""); }} className={selectCls + " flex-1"}>
              {/* only providers the server can authenticate (+ ollama, + current) */}
              {providers.filter((p) => p.key_set || p.name === "ollama" || p.name === provider)
                .map((p) => <option key={p.name} value={p.name}>{p.name}{p.key_set ? "" : " (no key)"}</option>)}
            </select>
            <div className="flex-1">
              <ModelPicker provider={provider} value={model} onChange={setModel} placeholder="model (default)" />
            </div>
            <Button size="sm" className="h-7 gap-1.5 font-mono text-[11px]" onClick={relaunch} disabled={busy || !experiment}>
              {busy ? <Loader2 className="size-3 animate-spin" /> : <Play className="size-3" />} re-run
            </Button>
          </div>
          {/* live Copilot budget — answers "do I still have budget for gpt-4o?" */}
          <CopilotQuotaChip provider={(providers.find((p) => p.name === provider)?.provider) || provider} />
          {msg && <div className="font-mono text-[11px] text-muted-foreground">{msg}</div>}
        </div>
      )}
    </div>
  );
}

// Pick an EXPERIMENT (a saved run config — e.g. the domain SI suite) and launch
// the FULL suite, then jump the view to its live run folder. This is the clear
// "which experiment do I want to run" control the homepage editor also exposes,
// surfaced here so you can pick → run → watch without leaving the runs view.
function ExperimentRunner() {
  const router = useRouter();
  const [exps, setExps] = useState([]);
  const [sel, setSel] = useState("");
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = async () => {
    try {
      const j = await (await fetch("/api/experiments", { cache: "no-store" })).json();
      const list = j.experiments || [];
      setExps(list);
      setSel((s) => s || list[0]?.name || "");
    } catch {}
  };
  const pollStatus = async () => {
    try { setStatus(await (await fetch("/api/run", { cache: "no-store" })).json()); } catch {}
  };
  useEffect(() => { load(); pollStatus(); const t = setInterval(pollStatus, 2500); return () => clearInterval(t); }, []);

  const running = status?.running ? status.info?.experiment : null;
  const selExp = exps.find((e) => e.name === sel);

  const run = async () => {
    if (!sel) return;
    setBusy(true); setMsg("");
    try {
      const j = await (await fetch(`/api/experiments/${sel}?action=run`, { method: "POST" })).json();
      if (j.ok) {
        setMsg("running…"); setOpen(false);
        // jump the view to this experiment's live run folder so it streams here
        router.push(`/runs?dir=${encodeURIComponent(`bench/experiments/${sel}/runs`)}`);
      } else setMsg(j.error || "run failed");
    } catch (e) { setMsg(String(e)); } finally { setBusy(false); pollStatus(); }
  };
  const cont = async () => {
    if (!sel) return;
    setBusy(true); setMsg("");
    try {
      const j = await (await fetch(`/api/experiments/${sel}?action=continue`, { method: "POST" })).json();
      if (j.ok) {
        setMsg("continuing…"); setOpen(false);
        router.push(`/runs?dir=${encodeURIComponent(`bench/experiments/${sel}/runs`)}`);
      } else setMsg(j.error || "continue failed");
    } catch (e) { setMsg(String(e)); } finally { setBusy(false); pollStatus(); }
  };
  const stop = async () => {
    try { await fetch(`/api/experiments/${running}?action=stop`, { method: "POST" }); setMsg("stopping…"); }
    catch (e) { setMsg(String(e)); } finally { pollStatus(); }
  };

  const selectCls = "h-7 rounded-md border border-input bg-transparent px-1.5 font-mono text-[11px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";
  return (
    <div className="relative">
      <button onClick={() => setOpen((v) => !v)} title="pick an experiment and run the full suite"
        className="flex items-center gap-1.5 rounded-sm border px-2 py-1 font-mono text-[11px] hover:bg-accent">
        <FlaskConical className="size-3.5" /> run experiment <ChevronDown className="size-3" />
      </button>
      {open && (
        <div className="absolute top-9 right-0 z-20 w-[26rem] rounded-md border bg-popover p-3 shadow-md space-y-2">
          <div className="font-mono text-[11px] text-muted-foreground">
            choose an experiment (a saved run config) and launch its FULL suite
          </div>
          <div className="flex items-center gap-2">
            <select value={sel} onChange={(e) => setSel(e.target.value)} className={selectCls + " flex-1"}>
              {exps.length === 0 && <option value="">no experiments — create one on the homepage</option>}
              {exps.map((e) => (
                <option key={e.name} value={e.name}>
                  {e.name} · {e.provider}{e.model ? `:${e.model}` : ""} · {e.testcases?.split("/").pop()}
                </option>
              ))}
            </select>
            {running === sel
              ? <Button size="sm" variant="destructive" className="h-7 gap-1.5 font-mono text-[11px]" onClick={stop}><Square className="size-3" /> stop</Button>
              : <>
                  {(selExp?.runs ?? 0) > 0 && (
                    <Button size="sm" variant="outline" className="h-7 gap-1.5 font-mono text-[11px]" onClick={cont} disabled={busy || !sel}
                      title="resume the latest run of this experiment: skip done cases, re-run the interrupted/missing ones">
                      {busy ? <Loader2 className="size-3 animate-spin" /> : <RotateCcw className="size-3" />} continue
                    </Button>
                  )}
                  <Button size="sm" className="h-7 gap-1.5 font-mono text-[11px]" onClick={run} disabled={busy || !sel}>
                    {busy ? <Loader2 className="size-3 animate-spin" /> : <Play className="size-3" />} run
                  </Button>
                </>}
          </div>
          {selExp && (
            <div className="font-mono text-[11px] text-muted-foreground">
              {(selExp.backends || []).join("+")} · steps {selExp.maxSteps} · att {selExp.maxAttempts} · {selExp.runs ?? 0} prior run{selExp.runs === 1 ? "" : "s"}
              {" · "}
              <a className="underline underline-offset-2 hover:text-foreground"
                 href={`/runs?dir=${encodeURIComponent(`bench/experiments/${sel}/runs`)}`}>watch latest</a>
            </div>
          )}
          <div className="font-mono text-[10px] text-muted-foreground">
            edit / create experiments on the <a href="/" className="underline underline-offset-2 hover:text-foreground">homepage</a>.
          </div>
          {msg && <div className="font-mono text-[11px] text-muted-foreground">{msg}</div>}
        </div>
      )}
    </div>
  );
}

function MiniScore({ be, t }) {
  const pct = t.n ? Math.round((100 * t.ok) / t.n) : 0;
  return (
    <div className="flex items-center gap-2">
      <span className={cn("font-mono text-xs uppercase tracking-wider", BE_COLOR[be])}>{be}</span>
      <span className="font-serif text-base leading-none">
        {t.ok}<span className="text-muted-foreground text-xs">/{t.n}</span>
      </span>
      <span className="hidden lg:inline font-mono text-[11px] text-muted-foreground">{pct}% · {fmtTokens(t.tok)}t</span>
    </div>
  );
}

export function TopBar({ live, fq, composition, compare, onToggleCompare, sidebarOpen, onToggleSidebar, runMeta, experiment, onRelaunched, adhoc }) {
  return (
    <header className="relative flex items-center gap-4 px-5 h-14 border-b bg-sidebar shrink-0">
      <div className="flex items-center gap-2.5">
        <Button variant="ghost" size="icon" className="size-7 -ml-1.5 text-muted-foreground"
          onClick={onToggleSidebar} title={sidebarOpen ? "Hide the case list" : "Show the case list"}>
          {sidebarOpen ? <PanelLeftClose className="size-4" /> : <PanelLeftOpen className="size-4" />}
        </Button>
        <h1 className="font-serif text-lg font-semibold tracking-tight">Bench Observer</h1>
        <span className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
          <span className={cn("inline-block size-2 rounded-full", live ? "bg-ok animate-pulse" : "bg-muted-foreground/40")} />
          {live ? "live" : "idle"}
        </span>
      </div>

      <Separator orientation="vertical" className="h-6" />

      <div className="flex items-center gap-5">
        <MiniScore be="fq" t={fq} />
        <MiniScore be="composition" t={composition} />
      </div>

      <Separator orientation="vertical" className="h-6" />
      <RunControls runMeta={runMeta} experiment={experiment} onRelaunched={onRelaunched} adhoc={adhoc} />

      <div className="ml-auto flex items-center gap-1.5">
        <Button asChild variant="ghost" size="sm" className="h-7 gap-1.5 font-mono text-xs">
          <Link href="/" title="Home — provider login & status"><Home className="size-3.5" /> home</Link>
        </Button>
        <Legend />
        <Button variant={compare ? "secondary" : "ghost"} size="sm"
          className="h-7 gap-1.5 font-mono text-xs" onClick={onToggleCompare}
          title="Use the whole screen to compare both backends">
          {compare ? <PanelLeft className="size-3.5" /> : <Columns2 className="size-3.5" />}
          {compare ? "show list" : "compare"}
        </Button>
        <ExperimentRunner />
        <Button asChild variant="outline" size="sm" className="h-7 gap-1.5 font-mono text-xs">
          <Link href="/ask"><Plus className="size-3.5" /> Ask</Link>
        </Button>
      </div>
    </header>
  );
}

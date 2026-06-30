"use client";
import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { FlaskConical, GitBranch, Loader2, CheckCircle2, Circle, ExternalLink, Copy, Plus } from "lucide-react";
import { Experiments } from "./Experiments.js";

const POLL_MS = 2500;

// Home / landing page: shows each BYOK/BYOM provider's connection status and runs
// the GitHub Copilot OAuth device-flow login in the browser (the token is cached
// SERVER-side, never shown here). The experiment itself lives at /runs.
export default function Home() {
  const [providers, setProviders] = useState([]);
  const [anyUsable, setAnyUsable] = useState(true); // assume ok until told otherwise
  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/providers", { cache: "no-store" });
      const j = await r.json();
      setProviders(j.profiles || []);
      setAnyUsable(j.anyUsable !== false);
    } catch {}
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div className="min-h-screen bg-background">
      <header className="flex items-center gap-3 px-6 h-14 border-b bg-sidebar">
        <h1 className="font-serif text-lg font-semibold tracking-tight">FutuRaM Benchmark</h1>
        <span className="font-mono text-xs text-muted-foreground">fq vs composition</span>
        <div className="ml-auto flex items-center gap-2">
          <Button asChild variant="outline" size="sm" className="h-8 gap-1.5 font-mono text-xs">
            <Link href="/ask"><Plus className="size-3.5" /> Ask</Link>
          </Button>
          <Button asChild size="sm" className="h-8 gap-1.5 font-mono text-xs">
            <Link href="/runs"><FlaskConical className="size-3.5" /> Open the experiment</Link>
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-10 space-y-8">
        {!anyUsable && (
          <div className="rounded-md border border-amber-500/50 bg-amber-50 dark:bg-amber-950/20 p-4 space-y-2">
            <div className="font-serif text-base font-semibold text-amber-800 dark:text-amber-300">
              No language model is set up yet
            </div>
            <p className="font-mono text-xs text-amber-800/90 dark:text-amber-200/80">
              The bench needs an LLM to answer questions. Pick the easiest option for you:
            </p>
            <ul className="font-mono text-xs text-amber-800/90 dark:text-amber-200/80 list-disc pl-5 space-y-1">
              <li><b>GitHub Copilot</b> (if you have a subscription): scroll to
                <a href="#providers" className="underline underline-offset-2"> Providers &amp; keys</a> and click <b>Sign in with GitHub</b> — no key to copy.</li>
              <li><b>A cloud key you already have</b> (OpenAI, Anthropic, Gemini, Groq…):
                set its environment variable (e.g. <code className="bg-amber-500/10 px-1">ANTHROPIC_API_KEY</code>)
                where the observer runs, then restart. The names are listed under Providers &amp; keys.</li>
              <li><b>A free local model</b>: install <a href="https://ollama.com" target="_blank" rel="noreferrer" className="underline underline-offset-2">Ollama</a>,
                run <code className="bg-amber-500/10 px-1">ollama pull llama3.1:8b</code>, and make sure it&apos;s running on your machine.</li>
            </ul>
          </div>
        )}

        <section className="space-y-1">
          <h2 className="font-serif text-2xl font-semibold tracking-tight">Experiment</h2>
          <p className="font-mono text-xs text-muted-foreground">
            Run a full testcase suite against a provider, both backends, straight
            from here — it streams live into the experiment view. Below: connect a
            provider. Keys are read from the server&apos;s environment and never
            entered in the browser — except GitHub Copilot, which you sign into here.
          </p>
        </section>

        <Experiments providers={providers} />

        <section id="providers" className="space-y-1 pt-2 scroll-mt-4">
          <h2 className="font-serif text-xl font-semibold tracking-tight">Providers &amp; keys</h2>
          <p className="font-mono text-xs text-muted-foreground">
            Only providers connected here are offered when you pick a model. Set the
            named env var in the observer&apos;s environment (then restart it), or
            sign into GitHub Copilot below — no key is ever typed in the browser.
          </p>
        </section>

        <CopilotCard
          profile={providers.find((p) => p.provider === "copilot")}
          onChange={refresh}
        />

        <section className="space-y-2">
          <h3 className="font-mono text-xs uppercase tracking-wider text-muted-foreground">all providers</h3>
          <Card className="divide-y p-0">
            {providers.length === 0 && (
              <div className="px-4 py-6 text-center font-mono text-xs text-muted-foreground">loading…</div>
            )}
            {providers.map((p) => (
              <div key={p.name} className="flex items-center gap-3 px-4 py-3">
                {p.usable
                  ? <CheckCircle2 className="size-4 text-ok shrink-0" />
                  : <Circle className="size-4 text-muted-foreground/50 shrink-0" />}
                <div className="min-w-0">
                  <div className="font-mono text-sm">{p.name}
                    {p.provider !== p.name && <span className="text-muted-foreground"> · {p.provider}</span>}
                  </div>
                  <div className="font-mono text-[11px] text-muted-foreground truncate">
                    {p.model || "model set per-run"}
                    {p.key_env && <> · key <span className="text-foreground/70">${p.key_env}</span></>}
                  </div>
                </div>
                <div className="ml-auto font-mono text-[11px]">
                  {p.usable
                    ? <span className="text-ok">ready</span>
                    : p.provider === "ollama"
                      ? <span className="text-amber-600">daemon not reachable</span>
                      : p.can_login
                        ? <span className="text-amber-600">sign in above</span>
                        : <span className="text-muted-foreground">set ${p.key_env}</span>}
                </div>
              </div>
            ))}
          </Card>
        </section>
      </main>
    </div>
  );
}

// The Copilot card: starts the device flow, shows the user code + verification
// URL, polls until the server has the token, then flips to "connected".
function CopilotCard({ profile, onChange }) {
  const connected = profile?.key_set;
  const [flow, setFlow] = useState(null); // {user_code, verification_uri}
  const [status, setStatus] = useState(null); // pending|connected|expired|error
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const start = async () => {
    setBusy(true); setErr(""); setStatus("pending");
    try {
      const r = await fetch("/api/login/copilot", { method: "POST" });
      const j = await r.json();
      if (!r.ok) { setErr(j.error || "could not start login"); setStatus("error"); setBusy(false); return; }
      setFlow(j);
    } catch (e) { setErr(String(e)); setStatus("error"); setBusy(false); }
  };

  // poll while a flow is in progress
  useEffect(() => {
    if (!flow || status !== "pending") return;
    const t = setInterval(async () => {
      try {
        const r = await fetch("/api/login/copilot", { cache: "no-store" });
        const j = await r.json();
        if (j.status === "connected") {
          setStatus("connected"); setBusy(false); setFlow(null);
          clearInterval(t); onChange && onChange();
        } else if (j.status === "expired" || j.status === "error" || j.status === "idle") {
          // "idle" means the server lost the flow (e.g. a dev restart) — surface
          // it instead of spinning forever; the user just clicks sign-in again.
          setStatus(j.status);
          setErr(j.status === "idle"
            ? "login was interrupted on the server — click sign in again"
            : (j.error || "the code expired — try again"));
          setBusy(false); setFlow(null); clearInterval(t);
        }
      } catch {}
    }, (flow.interval || 5) * 1000);
    return () => clearInterval(t);
  }, [flow, status, onChange]);

  return (
    <Card className="p-5 space-y-4">
      <div className="flex items-center gap-3">
        <GitBranch className="size-5" />
        <div>
          <div className="font-serif text-lg font-semibold leading-none">GitHub Copilot</div>
          <div className="font-mono text-[11px] text-muted-foreground mt-1">
            OpenAI-compatible · uses your Copilot subscription
          </div>
        </div>
        <div className="ml-auto font-mono text-xs flex items-center gap-1.5">
          {connected
            ? <><CheckCircle2 className="size-4 text-ok" /> connected</>
            : <><Circle className="size-4 text-muted-foreground/50" /> not connected</>}
        </div>
      </div>

      {!flow && (
        <Button size="sm" className="gap-1.5 font-mono text-xs" onClick={start} disabled={busy}>
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <GitBranch className="size-3.5" />}
          {connected ? "Re-authenticate with GitHub" : "Sign in with GitHub"}
        </Button>
      )}

      {flow && (
        <div className="rounded-md border bg-muted/40 p-4 space-y-3">
          <div className="font-mono text-xs text-muted-foreground">
            1. Open the GitHub device page and 2. enter this code:
          </div>
          <div className="flex items-center gap-3">
            <code className="rounded bg-background border px-3 py-1.5 font-mono text-lg tracking-widest">
              {flow.user_code}
            </code>
            <Button variant="outline" size="sm" className="h-8 gap-1.5 font-mono text-xs"
              onClick={() => navigator.clipboard?.writeText(flow.user_code)}>
              <Copy className="size-3.5" /> copy
            </Button>
            <Button asChild variant="outline" size="sm" className="h-8 gap-1.5 font-mono text-xs">
              <a href={flow.verification_uri} target="_blank" rel="noreferrer">
                <ExternalLink className="size-3.5" /> open github.com
              </a>
            </Button>
          </div>
          <div className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> waiting for you to authorise…
          </div>
        </div>
      )}

      {status === "connected" && !flow && (
        <div className="font-mono text-xs text-ok">✓ signed in — Copilot is ready for runs.</div>
      )}
      {err && <div className="font-mono text-xs text-destructive">{err}</div>}

      <Separator />
      <div className="font-mono text-[11px] text-muted-foreground">
        Prefer the terminal? <span className="text-foreground/70">uv run bench/run_bench.py --login copilot</span> does
        the same thing — both share the token cache.
      </div>
    </Card>
  );
}

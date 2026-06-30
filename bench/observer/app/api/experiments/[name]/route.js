// A single experiment: read / update / delete / RUN. Running launches run_bench
// with the saved config into bench/experiments/<name>/runs/<timestamp>/ (its own
// folder), and points the observer's live view at that folder so it streams.
import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import { safeName, readExperiment } from "../route.js";

import { REPO, UV, benchEndpointArgs } from "../../../../lib/paths.js";
const EXP_DIR = `${REPO}/bench/experiments`;
const TESTCASES_DIR = `${REPO}/bench/testcases`;
const PROVIDERS_FILE = `${REPO}/bench/providers.json`;
const DEFAULT_MODEL = process.env.BENCH_ASK_MODEL || "gemma4:31b-cloud";

export const dynamic = "force-dynamic";

// one run at a time, tracked across dev reloads
function state() { globalThis.__benchExpRun ||= { child: null, info: null }; return globalThis.__benchExpRun; }

// A killed/crashed run leaves its in-flight case file at status:"running" (the
// child cannot rewrite it on exit). Once the child is gone, reconcile every
// leftover "running" file in the run's own folder to status:"interrupted" — a
// terminal state the UI renders as a stopped case and the resume (--continue)
// path treats as not-done, so it re-runs.
async function reconcileInterrupted(runDir) {
  if (!runDir) return;
  let files = [];
  try { files = await fs.readdir(runDir); } catch { return; }
  for (const f of files) {
    if (!f.endsWith(".json")) continue;
    const p = path.join(runDir, f);
    try {
      const raw = JSON.parse(await fs.readFile(p, "utf8"));
      if (raw.status === "running") {
        raw.status = "interrupted";
        await fs.writeFile(p, JSON.stringify(raw, null, 2));
      }
    } catch { /* mid-write or non-case json — skip */ }
  }
}

async function knownProviders() {
  try {
    const raw = JSON.parse(await fs.readFile(PROVIDERS_FILE, "utf8"));
    return new Set(Object.keys(raw).filter((k) => !k.startsWith("_")).concat("ollama"));
  } catch { return new Set(["ollama"]); }
}
async function validTestcase(file) {
  const base = path.basename(file || "");
  if (!base || !/\.(ya?ml)$/.test(base)) return null;
  try { await fs.access(path.join(TESTCASES_DIR, base)); return `bench/testcases/${base}`; }
  catch { return null; }
}
function num(v, def, lo, hi) { const n = Number(v); return Number.isFinite(n) ? Math.max(lo, Math.min(hi, Math.round(n))) : def; }

export async function GET(_req, { params }) {
  const name = safeName((await params).name);
  const cfg = name && await readExperiment(name);
  if (!cfg) return Response.json({ error: "not found" }, { status: 404 });
  // include run history (folder names + their meta if present)
  let runs = [];
  try {
    const ents = await fs.readdir(path.join(EXP_DIR, name, "runs"), { withFileTypes: true });
    runs = ents.filter((e) => e.isDirectory()).map((e) => e.name).sort().reverse();
  } catch {}
  return Response.json({ ...cfg, runs });
}

export async function DELETE(_req, { params }) {
  const name = safeName((await params).name);
  if (!name) return Response.json({ error: "bad name" }, { status: 400 });
  // a DELETE with ?stop=1 stops the running child instead of deleting the dir
  // (kept simple: deletion removes the whole experiment folder)
  try { await fs.rm(path.join(EXP_DIR, name), { recursive: true, force: true }); }
  catch (e) { return Response.json({ error: String(e) }, { status: 500 }); }
  return Response.json({ ok: true, deleted: name });
}

// RUN the experiment (POST /api/experiments/<name>?action=run), RESUME an
// interrupted run (?action=continue — keeps the latest run folder, skips done
// cases, re-runs the rest), RE-RUN ONE case (?action=rerun-case&case=<id> — into
// the latest folder, leaving the other cases untouched), or STOP the in-progress
// run (?action=stop)
export async function POST(req, { params }) {
  const name = safeName((await params).name);
  if (!name) return Response.json({ error: "bad name" }, { status: 400 });
  const url = new URL(req.url);
  const action = url.searchParams.get("action") || "run";
  const s = state();

  if (action === "stop") {
    if (s.child && s.child.exitCode === null) {
      const runDir = s.info?.runDir;
      try { s.child.kill("SIGTERM"); } catch {}
      // give the child a moment to die, then mark its in-flight case interrupted
      // so the observer stops spinning and a later --continue re-runs it.
      setTimeout(() => { reconcileInterrupted(runDir).catch(() => {}); }, 1500);
      return Response.json({ ok: true, stopped: s.info?.id });
    }
    return Response.json({ ok: true, stopped: null });
  }

  if (s.child && s.child.exitCode === null) {
    return Response.json({ error: "a run is already in progress", id: s.info?.id }, { status: 409 });
  }

  const cfg = await readExperiment(name);
  if (!cfg) return Response.json({ error: "not found" }, { status: 404 });

  const tc = await validTestcase(cfg.testcases);
  if (!tc) return Response.json({ error: "experiment's testcases file is invalid" }, { status: 400 });
  const provider = (cfg.provider || "ollama").trim();
  if (provider !== "ollama" && !(await knownProviders()).has(provider)) {
    return Response.json({ error: `unknown provider '${provider}'` }, { status: 400 });
  }
  let backends = (Array.isArray(cfg.backends) ? cfg.backends : ["fq", "composition"])
    .filter((b) => b === "fq" || b === "composition");
  if (!backends.length) backends = ["fq", "composition"];

  // action=continue RESUMES the latest run folder (skip done cases, re-run the rest).
  // action=rerun-case re-runs ONE case (?case=<id>) into the latest run folder,
  // keeping every other case's transcript untouched. Both reuse the latest folder.
  const resume = action === "continue";
  const rerunCase = action === "rerun-case" ? (url.searchParams.get("case") || "").trim() : "";
  if (action === "rerun-case" && !/^[A-Za-z0-9_.-]+$/.test(rerunCase)) {
    return Response.json({ error: "rerun-case needs a valid ?case=<id>" }, { status: 400 });
  }
  const reuseLatest = resume || !!rerunCase;
  let ts, runDir;
  if (reuseLatest) {
    let latest = null;
    try {
      const ents = await fs.readdir(path.join(EXP_DIR, name, "runs"), { withFileTypes: true });
      const subs = ents.filter((e) => e.isDirectory()).map((e) => e.name).sort();
      latest = subs.length ? subs[subs.length - 1] : null;
    } catch {}
    if (!latest) return Response.json({ error: "no run to re-use" }, { status: 400 });
    ts = latest;
    runDir = path.join(EXP_DIR, name, "runs", ts);
  } else {
    // timestamped run folder; ISO with ':' replaced so it's a valid dir name
    ts = new Date().toISOString().replace(/[:.]/g, "-");
    runDir = path.join(EXP_DIR, name, "runs", ts);
  }
  await fs.mkdir(runDir, { recursive: true });
  const id = `${name}__${ts}`;

  // escalation: on failure, retry once with a better model on the listed backends
  const esc = cfg.escalate && Array.isArray(cfg.escalate.backends) && cfg.escalate.backends.length
    ? cfg.escalate : null;
  const escBackends = esc ? esc.backends.filter((b) => b === "fq" || b === "composition") : [];

  const args = [
    "run", "bench/run_bench.py", tc,
    ...benchEndpointArgs(),
    "--backends", backends.join(","),
    ...(provider !== "ollama"
      ? ["--provider", provider, ...(cfg.model ? ["--model", cfg.model] : [])]
      : ["--model", cfg.model || DEFAULT_MODEL]),
    ...(cfg.skills !== false ? ["--skills"] : []),
    "--verbose",   // per-case progress lines land in run.log for diagnosability
    "--max-steps", String(num(cfg.maxSteps, 16, 1, 64)),
    "--max-attempts", String(num(cfg.maxAttempts, 3, 1, 10)),
    "--token-budget", String(num(cfg.tokenBudget, 400000, 1000, 5000000)),
    "--timeout", String(num(cfg.timeout, 900, 30, 7200)),
    // per-experiment LLM sampling temperature (default 0.0 = deterministic); only
    // pass it when the experiment set one, so an unset config stays at the default.
    ...(cfg.temperature != null && cfg.temperature !== ""
      ? ["--temperature", String(num(cfg.temperature, 0, 0, 2))]
      : []),
    ...(resume ? ["--continue"] : []),
    // per-case re-run: run ONLY this case, and KEEP the other live transcripts in
    // the folder (don't clear) so re-running one case never wipes the rest.
    ...(rerunCase ? ["--only", rerunCase, "--keep-live"] : []),
    // escalation args (only when escalation is configured with ≥1 backend)
    ...(esc && escBackends.length ? [
      "--escalate-provider", esc.provider || "ollama",
      ...(esc.model ? ["--escalate-model", esc.model] : []),
      "--escalate-backends", escBackends.join(","),
    ] : []),
    // a per-case re-run must NOT overwrite the full run's results.json/zip — give it
    // its own side files; the canonical run files stay as the full run left them.
    "--json", path.join(runDir, rerunCase ? `results_rerun_${rerunCase}.json` : "results.json"),
    "--transcripts", path.join(runDir, rerunCase ? `transcripts_rerun_${rerunCase}.zip` : "transcripts.zip"),
    // stream THIS run's transcripts into its own folder; the observer points its
    // live view here (see ?dir= on the runs API) so it shows the right run.
    "--live-dir", runDir,
  ];
  // capture stdout+stderr to run.log in the run folder so a fast-failing run
  // (bad args, missing dep, auth) leaves a diagnosable record.
  const logFd = await fs.open(path.join(runDir, "run.log"), "a");
  const child = spawn(UV, args, {
    cwd: REPO,
    env: { ...process.env, BENCH_LIVE_DIR: runDir },
    detached: false, stdio: ["ignore", logFd.fd, logFd.fd],
  });
  child.on("exit", (code) => {
    logFd.close().catch(() => {});
    s.info && (s.info.exitCode = code);
    // a non-zero / killed exit can leave a case frozen at "running"; reconcile it
    // to "interrupted" so the UI doesn't spin and --continue knows to re-run it.
    if (code !== 0) reconcileInterrupted(runDir).catch(() => {});
  });
  s.child = child;
  s.info = { id, experiment: name, runDir, ts, provider, model: cfg.model || DEFAULT_MODEL,
             backends, testcases: tc, startedAt: Date.now(),
             temperature: (cfg.temperature != null && cfg.temperature !== "")
               ? num(cfg.temperature, 0, 0, 2) : 0 };
  return Response.json({ ok: true, ...s.info });
}

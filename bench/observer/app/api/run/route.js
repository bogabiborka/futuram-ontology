// Launch (and stop) a full benchmark SUITE run from the browser — the real
// experiment, no CLI. Spawns run_bench.py over a chosen testcases YAML with a
// chosen provider/model/backends and tunable loop params; transcripts stream into
// the shared live dir so the run shows up in /runs immediately.
//
// Security: testcases path is validated against the actual bench/testcases dir
// and provider against providers.json — the body can only pick CONFIGURED inputs,
// never inject arbitrary flags. API keys are NOT taken from the browser; the
// spawned process inherits the server's env (and the cached Copilot OAuth token).
import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";

import { REPO, UV, benchEndpointArgs } from "../../../lib/paths.js";
const LIVE_DIR = process.env.BENCH_LIVE_DIR || `${REPO}/bench/live`;
const TESTCASES_DIR = `${REPO}/bench/testcases`;
const PROVIDERS_FILE = `${REPO}/bench/providers.json`;
const DEFAULT_MODEL = process.env.BENCH_ASK_MODEL || "gemma4:31b-cloud";

export const dynamic = "force-dynamic";

// One suite run at a time (heavy). Tracked across module reloads via globalThis so
// a dev recompile doesn't lose the handle.
function state() {
  globalThis.__benchRun ||= { child: null, info: null };
  return globalThis.__benchRun;
}

async function knownProviders() {
  try {
    const raw = JSON.parse(await fs.readFile(PROVIDERS_FILE, "utf8"));
    return new Set(Object.keys(raw).filter((k) => !k.startsWith("_")).concat("ollama"));
  } catch {
    return new Set(["ollama"]);
  }
}

async function validTestcase(file) {
  // accept either a bare filename or a bench/testcases/<file> path; must exist in
  // the testcases dir and be a .yaml/.yml (no traversal).
  const base = path.basename(file || "");
  if (!base || !/\.(ya?ml)$/.test(base)) return null;
  try {
    await fs.access(path.join(TESTCASES_DIR, base));
    return `bench/testcases/${base}`;
  } catch {
    return null;
  }
}

// clamp a numeric body field into a sane range, falling back to a default
function num(v, def, lo, hi) {
  const n = Number(v);
  if (!Number.isFinite(n)) return def;
  return Math.max(lo, Math.min(hi, Math.round(n)));
}

export async function POST(req) {
  const s = state();
  if (s.child && s.child.exitCode === null) {
    return Response.json({ error: "a run is already in progress", id: s.info?.id }, { status: 409 });
  }

  let body;
  try { body = await req.json(); } catch { return Response.json({ error: "bad json" }, { status: 400 }); }

  const tc = await validTestcase(body.testcases);
  if (!tc) return Response.json({ error: "unknown or invalid testcases file" }, { status: 400 });

  const provider = (body.provider || "").trim();
  const model = (body.model || "").trim();
  if (provider && provider !== "ollama") {
    if (!(await knownProviders()).has(provider)) {
      return Response.json({ error: `unknown provider '${provider}'` }, { status: 400 });
    }
  }
  // backends: subset of fq,composition
  let backends = Array.isArray(body.backends) ? body.backends : String(body.backends || "fq,composition").split(",");
  backends = backends.map((b) => b.trim()).filter((b) => b === "fq" || b === "composition");
  if (!backends.length) backends = ["fq", "composition"];

  const maxSteps = num(body.maxSteps, 16, 1, 64);
  const maxAttempts = num(body.maxAttempts, 3, 1, 10);
  const tokenBudget = num(body.tokenBudget, 400000, 1000, 5000000);
  const timeout = num(body.timeout, 900, 30, 7200);
  const skills = body.skills !== false; // default on

  const id = "suite_" + Date.now().toString(36);
  const args = [
    "run", "bench/run_bench.py", tc,
    ...benchEndpointArgs(),
    "--backends", backends.join(","),
    ...(provider && provider !== "ollama"
      ? ["--provider", provider, ...(model ? ["--model", model] : [])]
      : ["--model", model || DEFAULT_MODEL]),
    ...(skills ? ["--skills"] : []),
    "--max-steps", String(maxSteps),
    "--max-attempts", String(maxAttempts),
    "--token-budget", String(tokenBudget),
    "--timeout", String(timeout),
    "--json", path.join(REPO, "bench", `results_${id}.json`),
    "--transcripts", path.join(REPO, "bench", `transcripts_${id}.zip`),
    "--live-dir", LIVE_DIR,
  ];

  const child = spawn(UV, args, {
    cwd: REPO,
    env: { ...process.env, BENCH_LIVE_DIR: LIVE_DIR },
    detached: false,
    stdio: "ignore",
  });
  s.child = child;
  s.info = { id, testcases: tc, provider: provider || "ollama", model: model || DEFAULT_MODEL,
             backends, maxSteps, maxAttempts, tokenBudget, timeout, startedAt: Date.now() };
  child.on("exit", () => { /* keep info for status; child.exitCode flips */ });

  return Response.json({ ok: true, ...s.info });
}

// status of the current/last suite run
export async function GET() {
  // Report whichever runner is active: this route's own suite run (__benchRun)
  // OR the experiment runner (__benchExpRun, in /api/experiments/[name]). They
  // are separate trackers; the UI polls THIS endpoint for "is anything running",
  // so an experiment run must show up here too.
  const s = state();
  const exp = globalThis.__benchExpRun || { child: null, info: null };
  const sRunning = !!(s.child && s.child.exitCode === null);
  const expRunning = !!(exp.child && exp.child.exitCode === null);
  const active = expRunning ? exp : s;
  return Response.json({
    running: sRunning || expRunning,
    info: active.info || null,
    exitCode: active.child ? active.child.exitCode : null,
  });
}

// stop the in-progress run
export async function DELETE() {
  const s = state();
  if (s.child && s.child.exitCode === null) {
    try { s.child.kill("SIGTERM"); } catch {}
    return Response.json({ ok: true, stopped: s.info?.id || null });
  }
  return Response.json({ ok: true, stopped: null, note: "nothing running" });
}

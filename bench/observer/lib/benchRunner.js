// BenchRunner — ONE class for launching `run_bench.py`, used as TWO INSTANCES whose
// DIFFERENCE is the answer-acceptance behaviour:
//
//   askRunner         — ad-hoc questions, ASK MODE: the FIRST grounded answer counts
//                       (acceptFirst → run_bench --accept-first / ask mode), nothing
//                       is scored.
//   experimentRunner  — suite/scored runs: when an answer is WRONG the loop gives
//                       corrective feedback and re-prompts (acceptFirst = false).
//
// Run state is FILESYSTEM-DERIVED — no in-memory child handles, no globalThis. On
// launch we record the child PID into the run's meta.json; "is it running?" is a
// live `kill(pid, 0)` probe, and "stop" sends SIGTERM to that PID. So the routes are
// stateless and survive a Next dev hot-reload with nothing to lose.
import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";

import { REPO, UV, benchEndpointArgs } from "./paths.js";

function num(v, def, lo, hi) {
  const n = Number(v);
  if (!Number.isFinite(n)) return def;
  return Math.max(lo, Math.min(hi, Math.round(n)));
}

// Does a process with this pid currently exist? (signal 0 = existence probe.)
export function pidAlive(pid) {
  if (!pid) return false;
  try { process.kill(pid, 0); return true; }
  catch (e) { return e.code === "EPERM"; }   // EPERM = exists but not ours; ESRCH = gone
}

export class BenchRunner {
  // ask: true  => ad-hoc ask mode, FIRST answer counts (acceptFirst).
  // ask: false => scored, re-prompt-on-wrong (the experiment behaviour).
  constructor({ ask = false, defaults = {} } = {}) {
    this.ask = ask;
    this.defaults = { maxSteps: 16, maxAttempts: 3, tokenBudget: 400000, timeout: 900, ...defaults };
  }

  _metaPath(runDir) { return path.join(runDir, "meta.json"); }

  async _readMeta(runDir) {
    try { return JSON.parse(await fs.readFile(this._metaPath(runDir), "utf8")); }
    catch { return null; }
  }

  /** Is the run in `runDir` currently executing? (live PID probe from its meta.) */
  async isRunning(runDir) {
    const meta = await this._readMeta(runDir);
    return !!(meta && pidAlive(meta.pid));
  }

  /** Stop the run in `runDir` by SIGTERM-ing its recorded PID. Returns true if signalled. */
  async stop(runDir) {
    const meta = await this._readMeta(runDir);
    if (meta && pidAlive(meta.pid)) {
      try { process.kill(meta.pid, "SIGTERM"); return true; } catch { return false; }
    }
    return false;
  }

  /**
   * Launch run_bench for a run living in `runDir`. Writes/updates meta.json with the
   * child PID so state is filesystem-derived. opts:
   *   id, runDir (required)
   *   question            ask mode: the --ask text
   *   testcases           scored mode: the testcases yaml path
   *   backends            "fq,composition"
   *   provider, model     BYOK/BYOM
   *   expected            optional JSON ground truth
   *   skills              default true
   *   maxSteps, maxAttempts, tokenBudget, timeout  (fall back to this.defaults)
   *   temperature         optional LLM sampling temperature (0.0 = deterministic)
   *   extraArgs           extra run_bench flags (e.g. --continue, escalation)
   *   meta                extra fields to persist into meta.json
   *   logFile             optional path to capture stdout+stderr
   */
  async launch(opts) {
    const d = this.defaults;
    const provider = (opts.provider || "").trim();
    const model = (opts.model || "").trim();
    const backends = (opts.backends || "fq,composition").trim();
    const timeout = num(opts.timeout, d.timeout, 30, 7200);
    const tokenBudget = num(opts.tokenBudget, d.tokenBudget, 1000, 5000000);

    await fs.mkdir(opts.runDir, { recursive: true });
    const args = [
      "run", "bench/run_bench.py",
      ...(this.ask ? ["--ask", opts.question, "--ask-id", opts.id] : [opts.testcases]),
      ...benchEndpointArgs(),
      "--backends", backends,
      ...(opts.expected ? ["--ask-expected", JSON.stringify(opts.expected)] : []),
      ...(provider && provider !== "ollama"
        ? ["--provider", provider, ...(model ? ["--model", model] : [])]
        : ["--model", model || process.env.BENCH_ASK_MODEL || "gemma4:31b-cloud"]),
      ...(opts.skills === false ? [] : ["--skills"]),
      "--max-steps", String(num(opts.maxSteps, d.maxSteps, 1, 64)),
      "--max-attempts", String(num(opts.maxAttempts, d.maxAttempts, 1, 10)),
      "--token-budget", String(tokenBudget),
      "--timeout", String(timeout),
      ...(opts.temperature != null && opts.temperature !== ""
        ? ["--temperature", String(num(opts.temperature, 0, 0, 2))]
        : []),
      ...(opts.extraArgs || []),
      "--transcripts", path.join(opts.runDir, `transcripts_${opts.id}.zip`),
      "--live-dir", opts.runDir,
    ];

    let stdio = "ignore";
    let logFd;
    if (opts.logFile) { logFd = await fs.open(opts.logFile, "a"); stdio = ["ignore", logFd.fd, logFd.fd]; }
    const child = spawn(UV, args, {
      cwd: REPO,
      env: { ...process.env, BENCH_LIVE_DIR: opts.runDir },
      detached: false,
      stdio,
    });
    if (logFd) child.on("exit", () => logFd.close().catch(() => {}));

    // record the PID into meta.json — this IS the run-state (no in-memory handle).
    const meta = {
      id: opts.id, backends, provider: provider || "ollama",
      model: model || process.env.BENCH_ASK_MODEL || "gemma4:31b-cloud",
      maxSteps: num(opts.maxSteps, d.maxSteps, 1, 64),
      maxAttempts: num(opts.maxAttempts, d.maxAttempts, 1, 10),
      tokenBudget, timeout,
      ...(opts.meta || {}),
      pid: child.pid, startedAt: Date.now(),
    };
    await fs.writeFile(this._metaPath(opts.runDir), JSON.stringify(meta, null, 2));
    return { pid: child.pid, meta };
  }
}

// The TWO instances — same machinery, different acceptance behaviour.
export const askRunner = new BenchRunner({ ask: true });
export const experimentRunner = new BenchRunner({ ask: false });

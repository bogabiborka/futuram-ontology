// Ad-hoc "ask" runs — a question answered on BOTH backends, REMEMBERED and
// RESTARTABLE. Each ask is a folder bench/asks/<id>/ with meta.json (question +
// model/provider/backends/limits + the child PID = its run-state) and the per-
// backend transcripts. Uses the shared askRunner (BenchRunner, ask mode: first
// answer counts). No in-memory tracking — run-state is the PID in meta.json.
//
//   POST  /api/ask                → new ask (creates folder, launches, returns meta)
//   POST  /api/ask?rerun=<id>     → re-run a stored ask's question (same folder)
//   GET   /api/ask                → history (all asks, newest first) + running ids
//   DELETE /api/ask?id=<id>       → stop + remove an ask
import { promises as fs } from "node:fs";
import path from "node:path";

import { REPO } from "../../../lib/paths.js";
import { ASKS_DIR } from "../../../lib/runs.js";
import { askRunner, pidAlive } from "../../../lib/benchRunner.js";
const PROVIDERS_FILE = `${REPO}/bench/providers.json`;

async function knownProviders() {
  try {
    const raw = JSON.parse(await fs.readFile(PROVIDERS_FILE, "utf8"));
    return new Set(Object.keys(raw).filter((k) => !k.startsWith("_")).concat("ollama"));
  } catch { return new Set(["ollama"]); }
}

const safeId = (s) => /^ask_[a-z0-9]+$/.test(String(s || "")) ? s : null;
const askDir = (id) => path.join(ASKS_DIR, id);
const num = (v, def, lo, hi) => {
  const n = Number(v);
  return Number.isFinite(n) ? Math.max(lo, Math.min(hi, Math.round(n))) : def;
};

export const dynamic = "force-dynamic";

// Clear a folder's transcripts (a re-run replaces the previous answer), keep meta.
async function clearTranscripts(dir) {
  try {
    for (const f of await fs.readdir(dir)) {
      if (f !== "meta.json") await fs.rm(path.join(dir, f), { force: true });
    }
  } catch {}
}

export async function POST(req) {
  const rerun = safeId(new URL(req.url).searchParams.get("rerun"));

  // ----- RE-RUN a stored ask: re-run its question in the SAME folder -----------
  if (rerun) {
    const dir = askDir(rerun);
    let meta;
    try { meta = JSON.parse(await fs.readFile(path.join(dir, "meta.json"), "utf8")); }
    catch { return Response.json({ error: "unknown ask" }, { status: 404 }); }
    if (await askRunner.isRunning(dir)) {
      return Response.json({ error: "this ask is already running", id: rerun }, { status: 409 });
    }
    await clearTranscripts(dir);
    const { meta: m2 } = await askRunner.launch({
      id: rerun, runDir: dir, question: meta.question, backends: meta.backends,
      provider: meta.provider, model: meta.model, expected: meta.expected || null,
      tokenBudget: meta.tokenBudget, timeout: meta.timeout,
      meta: { question: meta.question, expected: meta.expected || null,
              createdAt: meta.createdAt, rerunAt: Date.now() },
    });
    return Response.json({ ok: true, id: rerun, rerun: true, ...m2 });
  }

  // ----- NEW ask ----------------------------------------------------------------
  let body;
  try { body = await req.json(); } catch { return Response.json({ error: "bad json" }, { status: 400 }); }
  const question = (body.question || "").trim();
  const backends = (body.backends || "fq,composition").trim();
  const expected = body.expected && typeof body.expected === "object" ? body.expected : null;
  if (!question) return Response.json({ error: "question required" }, { status: 400 });

  const provider = (body.provider || "").trim();
  const model = (body.model || "").trim();
  if (provider && provider !== "ollama" && !(await knownProviders()).has(provider)) {
    return Response.json({ error: `unknown provider '${provider}'` }, { status: 400 });
  }
  // user-settable limits (clamped); fall back to the ask runner defaults.
  const tokenBudget = body.tokenBudget != null ? num(body.tokenBudget, 400000, 1000, 5000000) : undefined;
  const timeout = body.timeout != null ? num(body.timeout, 900, 30, 7200) : undefined;

  const id = "ask_" + Date.now().toString(36);
  const { meta } = await askRunner.launch({
    id, runDir: askDir(id), question, backends, provider, model, expected,
    tokenBudget, timeout,
    meta: { question, expected: expected || null, createdAt: Date.now() },
  });
  return Response.json({ ok: true, ...meta });
}

// History: every saved ask (newest first) + which ids are currently running.
export async function GET() {
  let ids = [];
  try {
    const ents = await fs.readdir(ASKS_DIR, { withFileTypes: true });
    ids = ents.filter((e) => e.isDirectory() && safeId(e.name)).map((e) => e.name);
  } catch {}
  const asks = [];
  const running = [];
  for (const id of ids) {
    try {
      const meta = JSON.parse(await fs.readFile(path.join(askDir(id), "meta.json"), "utf8"));
      asks.push(meta);
      if (pidAlive(meta.pid)) running.push(id);
    } catch {}
  }
  asks.sort((a, b) => (b.rerunAt || b.createdAt || 0) - (a.rerunAt || a.createdAt || 0));
  return Response.json({ asks, running });
}

// Delete a stored ask (stop it first if running).
export async function DELETE(req) {
  const id = safeId(new URL(req.url).searchParams.get("id"));
  if (!id) return Response.json({ error: "bad id" }, { status: 400 });
  await askRunner.stop(askDir(id));
  try { await fs.rm(askDir(id), { recursive: true, force: true }); }
  catch (e) { return Response.json({ error: String(e) }, { status: 500 }); }
  return Response.json({ ok: true, deleted: id });
}

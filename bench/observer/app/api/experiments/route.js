// Experiments = named, editable, saved run configurations. Each lives in
// bench/experiments/<name>/experiment.json; its runs are archived under
// bench/experiments/<name>/runs/<timestamp>/. This route is the collection:
//   GET            list all experiments (+ run-folder counts)
//   POST {config}  create or overwrite an experiment (upsert by name)
// The single-experiment route ([name]) handles read/update/delete/run.
//
// A config holds NO secrets — only the provider PROFILE name (the key lives in
// the server env / cached token, never here).
import { promises as fs } from "node:fs";
import path from "node:path";

import { REPO } from "../../../lib/paths.js";
const EXP_DIR = `${REPO}/bench/experiments`;

export const dynamic = "force-dynamic";

// kebab-safe name so it's a valid, traversal-free directory
export function safeName(name) {
  const s = String(name || "").trim().toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
  return s || null;
}

const DEFAULT_CONFIG = {
  testcases: "bench/testcases/domain.yaml",
  provider: "ollama",
  model: "",
  backends: ["fq", "composition"],
  skills: true,
  maxSteps: 16,
  maxAttempts: 3,
  tokenBudget: 400000,
  timeout: 900,
};

export async function readExperiment(name) {
  const f = path.join(EXP_DIR, name, "experiment.json");
  try {
    const cfg = JSON.parse(await fs.readFile(f, "utf8"));
    return { ...DEFAULT_CONFIG, ...cfg, name };
  } catch {
    return null;
  }
}

async function runCount(name) {
  try {
    const dirs = await fs.readdir(path.join(EXP_DIR, name, "runs"), { withFileTypes: true });
    return dirs.filter((d) => d.isDirectory()).length;
  } catch {
    return 0;
  }
}

export async function GET() {
  let names = [];
  try {
    const ents = await fs.readdir(EXP_DIR, { withFileTypes: true });
    names = ents.filter((e) => e.isDirectory()).map((e) => e.name);
  } catch {
    return Response.json({ experiments: [] });
  }
  const experiments = [];
  for (const n of names.sort()) {
    const cfg = await readExperiment(n);
    if (cfg) experiments.push({ ...cfg, runs: await runCount(n) });
  }
  return Response.json({ experiments });
}

export async function POST(req) {
  let body;
  try { body = await req.json(); } catch { return Response.json({ error: "bad json" }, { status: 400 }); }
  const name = safeName(body.name);
  if (!name) return Response.json({ error: "a valid name is required" }, { status: 400 });

  const cfg = {
    testcases: String(body.testcases || DEFAULT_CONFIG.testcases),
    provider: String(body.provider || DEFAULT_CONFIG.provider),
    model: String(body.model || ""),
    backends: Array.isArray(body.backends) && body.backends.length ? body.backends : DEFAULT_CONFIG.backends,
    skills: body.skills !== false,
    maxSteps: Number(body.maxSteps) || DEFAULT_CONFIG.maxSteps,
    maxAttempts: Number(body.maxAttempts) || DEFAULT_CONFIG.maxAttempts,
    tokenBudget: Number(body.tokenBudget) || DEFAULT_CONFIG.tokenBudget,
    timeout: Number(body.timeout) || DEFAULT_CONFIG.timeout,
    note: String(body.note || ""),
  };
  const dir = path.join(EXP_DIR, name);
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(path.join(dir, "experiment.json"), JSON.stringify(cfg, null, 2));
  return Response.json({ ok: true, name, ...cfg });
}

// List the model ids a provider/profile offers, for the UI's model dropdown.
// Shells out to `run_bench.py --list-provider-models <provider>` (which reuses the
// same key/token resolution as a run — including the cached Copilot OAuth token).
// Results are cached briefly per provider so the dropdown is snappy and we don't
// hammer the provider's /models endpoint on every keystroke.
import { spawn } from "node:child_process";

import { REPO, UV, benchEndpointArgs } from "../../../lib/paths.js";
const CACHE = new Map(); // provider -> { at, models }
const TTL_MS = 60_000;

export const dynamic = "force-dynamic";

function listModels(provider) {
  return new Promise((resolve) => {
    let out = "";
    const child = spawn(UV, ["run", "bench/run_bench.py", ...benchEndpointArgs(),
      "--list-provider-models", provider], { cwd: REPO, env: process.env });
    child.stdout.on("data", (d) => (out += d));
    child.on("error", () => resolve([]));
    child.on("close", () => {
      try { resolve(JSON.parse(out.trim().split("\n").pop()).models || []); }
      catch { resolve([]); }
    });
  });
}

export async function GET(req) {
  const provider = (new URL(req.url).searchParams.get("provider") || "").trim();
  if (!/^[a-z0-9_-]+$/i.test(provider)) {
    return Response.json({ error: "bad provider" }, { status: 400 });
  }
  const hit = CACHE.get(provider);
  if (hit && Date.now() - hit.at < TTL_MS) {
    return Response.json({ provider, models: hit.models, cached: true });
  }
  const models = await listModels(provider);
  CACHE.set(provider, { at: Date.now(), models });
  return Response.json({ provider, models });
}

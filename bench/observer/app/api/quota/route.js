// The account's live Copilot quota (plan, reset date, per-quota remaining), so
// the model picker can show the user their budget. Shells out to
// `run_bench.py --copilot-quota`, which reuses the same token resolution as a run
// (incl. the cached OAuth token). NEVER returns a key — only the quota numbers.
// Cached briefly so the chip doesn't hit the entitlement endpoint on every render.
import { spawn } from "node:child_process";

import { REPO, UV, benchEndpointArgs } from "../../../lib/paths.js";
let CACHE = null; // { at, data }
const TTL_MS = 60_000;

export const dynamic = "force-dynamic";

function fetchQuota() {
  return new Promise((resolve) => {
    let out = "";
    const child = spawn(UV, ["run", "bench/run_bench.py", ...benchEndpointArgs(),
      "--copilot-quota"], { cwd: REPO, env: process.env });
    child.stdout.on("data", (d) => (out += d));
    child.on("error", () => resolve(null));
    child.on("close", () => {
      try { resolve(JSON.parse(out.trim().split("\n").pop())); }
      catch { resolve(null); }
    });
  });
}

export async function GET() {
  if (CACHE && Date.now() - CACHE.at < TTL_MS) {
    return Response.json({ ...CACHE.data, cached: true });
  }
  const data = await fetchQuota();
  CACHE = { at: Date.now(), data };
  return Response.json({ ...(data || {}), cached: false });
}

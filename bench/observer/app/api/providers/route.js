// List the BYOK/BYOM provider profiles (names + their default model) from
// bench/providers.json, so the picker can offer them. NEVER returns keys — only
// profile names, providers, default models, and whether the server is configured
// to authenticate (key_set). The actual key lives in a server-side env var (or,
// for Copilot, a cached OAuth token) that the spawned run_bench inherits; it is
// never sent to or from the browser.
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

import { REPO } from "../../../lib/paths.js";
const PROVIDERS_FILE = `${REPO}/bench/providers.json`;

export const dynamic = "force-dynamic";

// Copilot can authenticate from COPILOT_API_KEY, GITHUB_TOKEN/GH_TOKEN, OR a
// cached OAuth token (shared with the CLI). Mirror benchlib._resolve_key.
async function copilotConnected(profile) {
  if (process.env[profile.key_env] || process.env.GITHUB_TOKEN || process.env.GH_TOKEN) return true;
  const base = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  try {
    const j = JSON.parse(await fs.readFile(path.join(base, "futuram-bench", "copilot.json"), "utf8"));
    return Boolean(j.access_token);
  } catch {
    return false;
  }
}

// Is the host Ollama reachable + does it have any model? (ollama needs no key,
// but it's only USABLE if the daemon answers). Probes /api/tags quickly.
async function ollamaUsable() {
  const host = (process.env.OLLAMA_HOST || "http://localhost:11434").replace(/\/$/, "");
  try {
    const ctl = AbortSignal.timeout(2500);
    const r = await fetch(`${host}/api/tags`, { signal: ctl });
    if (!r.ok) return false;
    const j = await r.json();
    return Array.isArray(j.models) && j.models.length > 0;
  } catch {
    return false;
  }
}

export async function GET() {
  let raw = {};
  try {
    raw = JSON.parse(await fs.readFile(PROVIDERS_FILE, "utf8"));
  } catch {
    raw = {};
  }
  const entries = Object.entries(raw).filter(
    ([k, v]) => v && typeof v === "object" && !k.startsWith("_"),
  );
  const ollamaOk = await ollamaUsable();
  const profiles = await Promise.all(
    entries.map(async ([name, v]) => {
      const provider = v.provider || name;
      let key_set;
      if (provider === "copilot") key_set = await copilotConnected(v);
      else if (provider === "ollama") key_set = true; // no key needed
      else key_set = v.key_env ? Boolean(process.env[v.key_env]) : true;
      // USABLE = can actually run today: a key/token is set AND (for ollama) the
      // daemon answers with at least one model.
      const usable = provider === "ollama" ? ollamaOk : key_set;
      return {
        name,
        provider,
        model: v.model || null,
        key_env: v.key_env || null,
        key_set,        // server has the credential
        usable,         // can run right now (ollama also needs a live daemon)
        can_login: provider === "copilot",
      };
    }),
  );
  if (!profiles.some((p) => p.name === "ollama")) {
    profiles.unshift({ name: "ollama", provider: "ollama", model: null, key_env: null,
                       key_set: true, usable: ollamaOk, can_login: false });
  }
  // overall: is ANYTHING usable? Drives the homepage "set up a model" banner.
  const anyUsable = profiles.some((p) => p.usable);
  return Response.json({ profiles, anyUsable, ollamaUsable: ollamaOk });
}

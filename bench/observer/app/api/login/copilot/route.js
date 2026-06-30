// GitHub Copilot OAuth device-flow login, run SERVER-SIDE so the token never
// touches the browser. POST starts the flow (returns the user_code +
// verification_uri to display); the browser then polls GET until the server has
// obtained and cached the GitHub OAuth token.
//
// The in-flight flow state AND the final token are persisted to disk rather than
// a module variable, so they survive Next.js dev recompiling the route module on
// edit/Fast Refresh. The token is cached to the SAME file the Python CLI uses
// (benchlib/oauth.py -> ~/.config/futuram-bench/copilot.json, {access_token}),
// so a login here works for `run_bench.py --provider copilot` and vice-versa.
// run_bench exchanges this GitHub token for the short-lived Copilot token at run
// time. The token is NEVER returned to the browser.
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

export const dynamic = "force-dynamic";

// Public client_id of the GitHub Copilot / VS Code OAuth app (not a secret — the
// device flow needs no client secret). Kept in sync with benchlib/oauth.py.
const CLIENT_ID = "Iv1.b507a08c87ecfe98";
const DEVICE_CODE_URL = "https://github.com/login/device/code";
const ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token";

function cfgDir() {
  const base = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  return path.join(base, "futuram-bench");
}
const TOKEN_FILE = () => path.join(cfgDir(), "copilot.json");
const PENDING_FILE = () => path.join(cfgDir(), ".copilot-device-flow.json");

async function writeJson(file, obj, mode) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, JSON.stringify(obj), mode ? { mode } : undefined);
  if (mode) { try { await fs.chmod(file, mode); } catch {} }
}
async function readJson(file) {
  try { return JSON.parse(await fs.readFile(file, "utf8")); } catch { return null; }
}

async function postForm(url, fields) {
  const r = await fetch(url, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "futuram-bench" },
    body: new URLSearchParams(fields).toString(),
  });
  return r.json();
}

export async function POST() {
  const start = await postForm(DEVICE_CODE_URL, { client_id: CLIENT_ID, scope: "read:user" });
  if (!start.device_code) {
    return Response.json({ error: start.error_description || start.error || "device code request failed" }, { status: 502 });
  }
  await writeJson(PENDING_FILE(), {
    device_code: start.device_code,
    interval: Number(start.interval || 5),
    deadline: Date.now() + Number(start.expires_in || 900) * 1000,
    last_poll: 0,   // epoch ms of the last GitHub token poll (rate-limit guard)
  });
  return Response.json({
    user_code: start.user_code,
    verification_uri: start.verification_uri,
    interval: Number(start.interval || 5),
    expires_in: start.expires_in,
  });
}

// Poll: returns {status: "idle"|"pending"|"connected"|"expired"|"error"}.
export async function GET() {
  const pending = await readJson(PENDING_FILE());
  if (!pending) return Response.json({ status: "idle" });
  if (Date.now() > pending.deadline) {
    await fs.rm(PENDING_FILE(), { force: true });
    return Response.json({ status: "expired" });
  }

  // Server-side rate limiting: GitHub rejects token polls faster than `interval`
  // with a `slow_down` error. The browser polls on its own timer, so throttle here
  // — if GitHub was polled less than `interval` seconds ago, return "pending"
  // without calling GitHub. This keeps the flow correct regardless of how often
  // the page GETs.
  const now = Date.now();
  const sinceLast = now - Number(pending.last_poll || 0);
  if (sinceLast < Number(pending.interval || 5) * 1000) {
    return Response.json({ status: "pending", interval: pending.interval });
  }
  pending.last_poll = now;
  await writeJson(PENDING_FILE(), pending);

  const resp = await postForm(ACCESS_TOKEN_URL, {
    client_id: CLIENT_ID,
    device_code: pending.device_code,
    grant_type: "urn:ietf:params:oauth:grant-type:device_code",
  });

  if (resp.access_token) {
    await writeJson(TOKEN_FILE(), { access_token: resp.access_token }, 0o600);
    await fs.rm(PENDING_FILE(), { force: true });
    return Response.json({ status: "connected" });
  }
  const err = resp.error;
  if (err === "authorization_pending") return Response.json({ status: "pending" });
  if (err === "slow_down") {
    // back off by GitHub's suggested interval, but DON'T compound the old one
    pending.interval = Number(resp.interval || pending.interval || 5) + 1;
    await writeJson(PENDING_FILE(), pending);
    return Response.json({ status: "pending", interval: pending.interval });
  }
  // terminal errors (expired_token / access_denied / …)
  await fs.rm(PENDING_FILE(), { force: true });
  return Response.json({ status: "error", error: resp.error_description || err || "login failed" });
}

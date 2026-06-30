// Read-only access to the bench live transcript directory. Every per-(case,
// backend) run is a JSON file <case>__<backend>.json that the harness rewrites
// after each attempt. Files come in two shapes — a "running" snapshot and a
// richer "completed" record — so we normalise both.
import { promises as fs } from "node:fs";
import path from "node:path";

import { REPO } from "./paths.js";
// ONE canonical live-transcript dir, shared with run_bench.py (resolve_live_dir).
// Both default to bench/live so a `run_bench` with no flag streams straight to
// this page — the recurring "page not updating" trap was the run writing dir A
// while the observer read dir B. Precedence: $BENCH_LIVE_DIR > bench/live.
const DEFAULT_LIVE_DIR = `${REPO}/bench/live`;
const LIVE_DIR = process.env.BENCH_LIVE_DIR || DEFAULT_LIVE_DIR;
const EXPERIMENTS_DIR = `${REPO}/bench/experiments`;
// Ad-hoc "ask" runs each get their OWN folder under here — bench/asks/<id>/ —
// holding meta.json + the per-backend transcripts. Persistent (remembered across
// restarts) and restartable, fully separate from the experiment run browser.
export const ASKS_DIR = process.env.BENCH_ASKS_DIR || `${REPO}/bench/asks`;
const TESTCASES =
  process.env.BENCH_TESTCASES || `${REPO}/bench/testcases/domain.yaml`;

// Resolve which transcript dir to read. Default = the shared live dir. An
// experiment run streams into bench/experiments/<name>/runs/<ts>/, so the UI can
// pass that path via ?dir=; we ONLY allow the default live dir or a path under
// bench/experiments (no traversal, no arbitrary filesystem read).
function resolveDir(dir) {
  if (!dir) return LIVE_DIR;
  // a relative ?dir= (e.g. "bench/experiments/<name>/runs/<ts>") is relative to
  // the REPO root, NOT the observer's CWD (which is bench/observer in the
  // container) — resolve against REPO so the allowlist check below matches.
  const abs = path.isAbsolute(dir) ? path.resolve(dir) : path.resolve(REPO, dir);
  if (abs === path.resolve(LIVE_DIR)) return abs;
  const asksRoot = path.resolve(ASKS_DIR);
  if (abs === asksRoot || abs.startsWith(asksRoot + path.sep)) return abs;   // an ask folder
  if (abs === EXPERIMENTS_DIR || abs.startsWith(EXPERIMENTS_DIR + path.sep)) return abs;
  return LIVE_DIR; // anything else falls back to the safe default
}

// The order the cases appear in the testcases YAML = the authoritative run order
// (we emit them in Supplementary-Information document order). The sidebar must
// PRESERVE that order, not sort alphabetically. We read the `- id:` lines in file
// order (no YAML dep needed) and map id -> rank. Re-read each call (cheap, file is
// tiny) so editing the testcases is reflected without a restart.
async function caseOrder() {
  try {
    const txt = await fs.readFile(TESTCASES, "utf8");
    const ids = [];
    for (const line of txt.split("\n")) {
      const m = line.match(/^\s*-\s*id:\s*(\S+)/);
      if (m) ids.push(m[1]);
    }
    const rank = new Map();
    ids.forEach((id, i) => rank.set(id, i));
    return rank;
  } catch {
    return new Map();
  }
}

function normalise(raw, file) {
  const conv = raw.conversation || [];
  // token total: prefer explicit, else sum the per-attempt token counts.
  const tokens =
    raw.tokens_total ??
    raw.tokens_so_far ??
    conv.reduce((a, c) => a + (c.tokens_in || 0) + (c.tokens_out || 0), 0);
  const attempts = raw.attempts ?? raw.attempts_so_far ?? conv.length;
  // status: completed records carry `correct`; running ones carry status:"running";
  // a crashed/connection-dropped case carries status:"error" (so it never shows as
  // a forever-"running" spinner). An explicit "error" status wins; otherwise derive
  // from `correct`.
  // explicit terminal statuses (error = crash/drop, interrupted = stopped/killed
  // mid-flight) win and never show as a forever-"running" spinner; otherwise derive
  // from `correct`.
  // ASK MODE: an ad-hoc "ask_*" question with NO ground truth set (expected has no
  // values/labels/names). There's nothing to be right/wrong against, so it must
  // NOT show a pass/fail verdict — neither in the sidebar marks nor the scoreboard
  // tally. Status is neutral: answered / no-answer (or running / error / interrupted).
  const exp = raw.expected || {};
  const hasGroundTruth = (Array.isArray(exp.values) && exp.values.length) ||
    (Array.isArray(exp.labels) && exp.labels.length) ||
    (Array.isArray(exp.names) && exp.names.length);
  const askMode = String(raw.case_id || "").startsWith("ask_") && !hasGroundTruth;

  let status = raw.status;
  if (status !== "error" && status !== "interrupted") {
    if (askMode) {
      status = raw.answer != null ? "answered" : "no-answer";
    } else {
      status = raw.correct === true ? "correct"
        : raw.correct === false ? "wrong"
        : "running";
    }
  }
  return {
    id: file.replace(/\.json$/, ""),
    case_id: raw.case_id,
    backend: raw.backend,
    question: raw.question || null,
    status,
    // ask-mode runs have no verdict: null out `correct` so the scoreboard tally
    // never counts them as right/wrong (it keys on correct===true/false).
    correct: askMode ? null : (raw.correct ?? null),
    askMode,
    attempts,
    tokens,
    seconds: raw.seconds ?? null,
    answer: raw.answer ?? null,
    expected: raw.expected ?? null,
    score_detail: raw.score_detail ?? null,
    // triaged error KIND (wrong-class / wrong-value / … ; "" when correct) so the
    // UI can show WHAT kind of error a fail is at a glance
    error_category: raw.error_category ?? null,
    subject_retries: raw.subject_retries ?? null,
    // the SPARQL the model's reported answer traced to (shown prominently in the UI)
    final_sparql: raw.final_sparql ?? null,
    // on ANY failed case (no-answer, not-grounded, OR a wrong value): the model's
    // own "why I failed" explanation, shown in the prominent slot INSTEAD of the query
    struggle_reason: raw.struggle_reason ?? null,
    // on a CASE CRASH / connection drop: the raw exception + full traceback, so the
    // observer shows WHY a case died instead of a forever-"running" spinner
    error: raw.error ?? null,
    traceback: raw.traceback ?? null,
    // which model/provider + loop params produced this run (live AND completed)
    run_meta: raw.run_meta ?? null,
    conversation: conv,
  };
}

// An experiment's "watch" link points at .../<name>/runs (the PARENT), but the
// transcripts live in .../runs/<timestamp>/. If the given dir holds no *.json but
// does hold timestamped run subfolders, descend into the NEWEST one — so a plain
// ".../runs" link shows the latest run without the caller knowing the timestamp.
async function withLatestRun(LD) {
  try {
    const ents = await fs.readdir(LD, { withFileTypes: true });
    if (ents.some((e) => e.isFile() && e.name.endsWith(".json"))) return LD;
    const subs = ents.filter((e) => e.isDirectory()).map((e) => e.name).sort();
    if (subs.length) return path.join(LD, subs[subs.length - 1]); // newest by ISO ts
  } catch {}
  return LD;
}

export async function listRuns(dir) {
  let LD = resolveDir(dir);
  if (!LD) return [];
  LD = await withLatestRun(LD);
  let files;
  try {
    files = await fs.readdir(LD);
  } catch {
    return [];
  }
  const out = [];
  for (const f of files) {
    if (!f.endsWith(".json")) continue;
    try {
      const txt = await fs.readFile(path.join(LD, f), "utf8");
      const raw = JSON.parse(txt);
      const n = normalise(raw, f);
      // drop the heavy conversation from the list payload
      const { conversation, ...summary } = n;
      summary.tool_calls = conversation.reduce(
        (a, c) => a + (c.messages || []).filter((m) => m.role === "tool").length,
        0,
      );
      out.push(summary);
    } catch {
      // file mid-write — skip this tick; it'll be valid next poll
    }
  }
  // Order by the testcases YAML (Supplementary-Information document order), NOT
  // alphabetically. Runs whose case isn't in the file (ad-hoc asks) sort last,
  // keeping their relative order. Backend tie-break: fq before composition.
  const rank = await caseOrder();
  const BIG = Number.MAX_SAFE_INTEGER;
  const beRank = (b) => (b === "fq" ? 0 : b === "composition" ? 1 : 2);
  out.sort((a, b) => {
    const ra = rank.has(a.case_id) ? rank.get(a.case_id) : BIG;
    const rb = rank.has(b.case_id) ? rank.get(b.case_id) : BIG;
    if (ra !== rb) return ra - rb;
    const back = beRank(a.backend) - beRank(b.backend);
    if (back !== 0) return back;
    return (a.case_id || "").localeCompare(b.case_id || "");
  });
  return out;
}

export async function getRun(id, dir) {
  let LD = resolveDir(dir);
  if (!LD) return null;
  LD = await withLatestRun(LD);   // descend into newest run if dir is a .../runs parent
  const safe = id.replace(/[^A-Za-z0-9_-]/g, "");
  try {
    const txt = await fs.readFile(path.join(LD, safe + ".json"), "utf8");
    return normalise(JSON.parse(txt), safe + ".json");
  } catch {
    return null;
  }
}

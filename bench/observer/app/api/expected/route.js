// Set the EXPECTED (ground-truth) answer for a case in the testcases YAML, from
// the website's answer editor. Writes via a small Python helper (uv + pyyaml) so
// the YAML is parsed and re-emitted safely rather than string-patched.
import { spawn } from "node:child_process";

import { REPO, UV } from "../../../lib/paths.js";
const TESTCASES =
  process.env.BENCH_TESTCASES || `${REPO}/bench/testcases/domain.yaml`;

export const dynamic = "force-dynamic";

// GET /api/expected?case_id=<id>[&file=<yaml>][&backend=<be>] — read a case's
// EXPECTED (ground truth) + question straight from the testcases YAML, the SOURCE OF
// TRUTH. Known BEFORE any run, so the UI doesn't wait for a backend to complete and
// copy `expected` onto the run record. Returns {expected, question, expected_by_backend}.
// `expected` is the backend-specific override when ?backend= matches one (some cases
// have a different IRI identity per backend), else the default.
export async function GET(req) {
  const url = new URL(req.url);
  const caseId = url.searchParams.get("case_id");
  if (!caseId) return Response.json({ error: "case_id required" }, { status: 400 });
  const file = url.searchParams.get("file");
  const backend = url.searchParams.get("backend") || "";
  const path = file
    ? (file.includes("/") ? `${REPO}/${file}` : `${REPO}/bench/testcases/${file}`)
    : TESTCASES;
  const py = `
import sys, json, yaml, pathlib
cid = ${JSON.stringify(caseId)}
backend = ${JSON.stringify(backend)}
doc = yaml.safe_load(pathlib.Path(${JSON.stringify(path)}).read_text()) or {}
cases = doc.get("cases", doc) if isinstance(doc, dict) else doc
hit = next((c for c in (cases or []) if str(c.get("id")) == cid), None)
out = {}
def _stamp(e):
    e = dict(e or {})
    if hit.get("score_uncertainty"): e.setdefault("score_uncertainty", True)
    if hit.get("ranked"): e.setdefault("ranked", True)
    return e
if hit is not None:
    ebb = {k: _stamp(v) for k, v in (hit.get("expected_by_backend") or {}).items()}
    default = _stamp(hit.get("expected"))
    chosen = ebb.get(backend, default)            # backend override or default
    out = {"expected": chosen, "question": hit.get("question"),
           "expected_by_backend": ebb}
print(json.dumps(out))
`;
  const { code, out } = await run(UV, ["run", "--with", "pyyaml", "python", "-c", py]);
  if (code !== 0) return Response.json({});
  try { return Response.json(JSON.parse(out.trim().split("\n").pop())); }
  catch { return Response.json({}); }
}

function run(cmd, args, input) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, { cwd: REPO });
    let out = "", err = "";
    p.stdout.on("data", (d) => (out += d));
    p.stderr.on("data", (d) => (err += d));
    p.on("close", (code) => resolve({ code, out, err }));
    if (input) { p.stdin.write(input); p.stdin.end(); }
  });
}

export async function POST(req) {
  let body;
  try { body = await req.json(); } catch { return Response.json({ error: "bad json" }, { status: 400 }); }
  const { case_id, expected, question } = body || {};
  if (!case_id || !expected) return Response.json({ error: "case_id and expected required" }, { status: 400 });

  // Python: load YAML, find the case, set its `expected`, write back. Reads the
  // payload {case_id, expected} from stdin.
  const py = `
import sys, json, yaml, pathlib
payload = json.load(sys.stdin)
path = pathlib.Path(${JSON.stringify(TESTCASES)})
doc = yaml.safe_load(path.read_text()) or {"cases": []}
if isinstance(doc, dict) and "cases" in doc:
    cases = doc["cases"]
elif isinstance(doc, list):
    cases = doc
else:
    doc = {"cases": []}; cases = doc["cases"]
hit = next((c for c in cases if str(c.get("id")) == str(payload["case_id"])), None)
if hit is None:
    # create the case if it does not exist yet (e.g. from an ad-hoc question)
    hit = {"id": str(payload["case_id"]),
           "question": payload.get("question") or str(payload["case_id"])}
    cases.append(hit)
    created = True
else:
    created = False
if payload.get("question"):
    hit["question"] = payload["question"]
hit["expected"] = payload["expected"]
# preserve the header comment block if present
text = path.read_text()
header = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#"))
body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)
path.write_text(header + body)
print(json.dumps({"ok": True, "case_id": payload["case_id"], "created": created}))
`;
  const { code, out, err } = await run(UV, ["run", "--with", "pyyaml", "python", "-c", py],
    JSON.stringify({ case_id, expected }));
  if (code !== 0) return Response.json({ error: err || out || "write failed" }, { status: 500 });
  try { return Response.json(JSON.parse(out.trim().split("\n").pop())); }
  catch { return Response.json({ ok: true }); }
}

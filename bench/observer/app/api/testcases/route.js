// List the testcase YAML files available to run as an experiment suite, with a
// cheap count of cases (number of "- id:" lines) so the UI can show suite sizes.
// Read-only.
import { promises as fs } from "node:fs";
import path from "node:path";

import { REPO } from "../../../lib/paths.js";
const TESTCASES_DIR = `${REPO}/bench/testcases`;

export const dynamic = "force-dynamic";

export async function GET() {
  let files = [];
  try {
    files = (await fs.readdir(TESTCASES_DIR)).filter((f) => f.endsWith(".yaml") || f.endsWith(".yml"));
  } catch {
    return Response.json({ testcases: [] });
  }
  const out = [];
  for (const f of files.sort()) {
    let cases = 0;
    try {
      const txt = await fs.readFile(path.join(TESTCASES_DIR, f), "utf8");
      cases = (txt.match(/^\s*-\s*id:/gm) || []).length;
    } catch {}
    out.push({ file: f, path: `bench/testcases/${f}`, cases });
  }
  return Response.json({ testcases: out });
}

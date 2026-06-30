import { listRuns } from "../../../lib/runs.js";

export const dynamic = "force-dynamic";   // never cache — always read fresh
export const revalidate = 0;

export async function GET(req) {
  // optional ?dir= points at an experiment's run folder; otherwise the shared
  // live dir. listRuns() validates the dir (only live or bench/experiments/*).
  const dir = new URL(req.url).searchParams.get("dir") || undefined;
  const runs = await listRuns(dir);
  return Response.json(
    { runs, ts: Date.now() },
    { headers: { "Cache-Control": "no-store" } },
  );
}

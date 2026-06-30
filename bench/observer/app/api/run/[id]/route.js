import { getRun } from "../../../../lib/runs.js";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET(req, { params }) {
  const { id } = await params;
  const dir = new URL(req.url).searchParams.get("dir") || undefined;
  const run = await getRun(id, dir);
  if (!run) return Response.json({ error: "not found" }, { status: 404 });
  return Response.json(run, { headers: { "Cache-Control": "no-store" } });
}

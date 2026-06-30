// Single source of truth for repo-relative paths + the bench service endpoints,
// so the observer works both on the host (npm run dev) and inside docker compose.
//
// In a container the repo is mounted at /repo and the bench services are reached
// by their compose hostnames; on the host it's the checkout dir and localhost.
// Everything is overridable by env so neither case is hardcoded.
import path from "node:path";

// Repo root: $BENCH_REPO, else two levels up from this file (bench/observer/lib
// -> repo root), so a plain `npm run dev` from a fresh clone needs no config.
export const REPO =
  process.env.BENCH_REPO || path.resolve(process.cwd(), "..", "..");

// run_bench is launched as `uv run bench/run_bench.py …` by default; override the
// launcher via $BENCH_PY (e.g. a venv python) if uv isn't on PATH.
export const RUN_BENCH = ["run", "bench/run_bench.py"];
export const UV = process.env.BENCH_UV || "uv";

// Bench HTTP endpoints the UI talks to / passes to run_bench. Default to the
// host ports; in compose they're set to the in-network service URLs.
export const MCP_URL = process.env.BENCH_MCP_URL || "http://localhost:47898/";
export const FQ_ENDPOINT =
  process.env.BENCH_FQ_ENDPOINT || "http://localhost:47040/query/sparql";
export const COMPOSITION_ENDPOINT =
  process.env.BENCH_COMPOSITION_ENDPOINT ||
  "http://localhost:47040/composition/sparql";

// CLI flags pinning run_bench to the right MCP + endpoints. On the host these are
// the defaults (so the flags are harmless); in compose the env points them at the
// in-network service URLs, which is what makes run_bench-inside-the-container work
// (it can't reach the host's localhost ports). Prepend to every spawn's args.
export function benchEndpointArgs() {
  return [
    "--mcp", MCP_URL,
    "--endpoint", `fq=${FQ_ENDPOINT}`,
    "--endpoint", `composition=${COMPOSITION_ENDPOINT}`,
    "--ollama-host", process.env.OLLAMA_HOST || "http://localhost:11434",
  ];
}

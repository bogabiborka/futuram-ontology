/** @type {import('next').NextConfig} */
const nextConfig = {
  outputFileTracingRoot: import.meta.dirname,
  // The live transcript directory the bench harness writes to. The API routes
  // read it on every request (read-only). Override with BENCH_LIVE_DIR.
  env: {
    BENCH_LIVE_DIR:
      process.env.BENCH_LIVE_DIR ||
      "/Users/graj/Documents/projects/fun/ont-rewrite/bench/transcripts_helpers.live",
    BENCH_LOG:
      process.env.BENCH_LOG || "/tmp/helpers_run.log",
  },
};
export default nextConfig;

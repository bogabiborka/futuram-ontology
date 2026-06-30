// Shared formatting + classification helpers used across the observer UI.

export const BACKENDS = ["fq", "composition"];

export const BE_COLOR = {
  fq: "text-blue",
  composition: "text-warn",
};

export function classifyTool(content) {
  const c = content || "";
  if (c.includes('"bindings"') || c.startsWith("Results of SPARQL")) return "data";
  if (c.includes("returned no results")) return "empty";
  if (c.includes("not valid") || c.includes("returned error")) return "invalid";
  return null;
}

export const fmtTokens = (n) =>
  n == null ? "—" : n >= 1000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + "k" : String(n);

export const fmtSecs = (s) =>
  s == null ? "—" : s < 60 ? `${s.toFixed(0)}s` : `${(s / 60).toFixed(1)}m`;

export const ST_BADGE = (status) =>
  status === "correct" ? { variant: "default", label: "✓ correct", cls: "bg-ok text-white" }
  : status === "wrong" ? { variant: "destructive", label: "✗ wrong", cls: "" }
  : status === "error" ? { variant: "destructive", label: "⚠ errored", cls: "bg-amber-600 text-white" }
  : { variant: "outline", label: "running", cls: "text-muted-foreground" };

export function shortArgs(a) {
  if (!a) return "";
  if (a.sparql_query) return "sparql…";
  if (a.skill_id) return a.skill_id;
  if (a.question) return `"${(a.question || "").slice(0, 36)}…"`;
  return Object.keys(a).join(", ");
}

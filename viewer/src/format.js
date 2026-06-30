export const fmtNum = (n) =>
  Number(n).toLocaleString(undefined, { maximumFractionDigits: 4 });

export const fmtSec = (s) =>
  s >= 60 ? `${(s / 60).toFixed(1)}m` : `${Number(s).toFixed(1)}s`;

export const fmtK = (n) =>
  n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

export const localName = (s) => {
  let t = String(s || "").trim();
  if (t.startsWith("<") && t.endsWith(">")) t = t.slice(1, -1);
  const local = t.split(/[#/]/).pop();
  return ((local || t).split(":").pop() || "").trim();
};

export function statusOf(c) {
  if (!c) return "pending";
  if (c.timed_out) return "timeout";
  if (c.token_capped) return "token-cap";
  if (c.error_category === "provider-error") return "provider-error";
  if (c.correct === true) return "correct";
  if (c.correct === false) return c.answer ? "wrong" : "no-answer";
  return "pending";
}

export const STATUS_COLORS = {
  correct: { bg: "#dcfce7", text: "#15803d", border: "#bbf7d0" },
  wrong: { bg: "#fee2e2", text: "#b91c1c", border: "#fecaca" },
  "no-answer": { bg: "#f3f4f6", text: "#6b7280", border: "#e5e7eb" },
  timeout: { bg: "#fef9c3", text: "#a16207", border: "#fef08a" },
  "token-cap": { bg: "#fef9c3", text: "#a16207", border: "#fef08a" },
  "provider-error": { bg: "#f3e8ff", text: "#7e22ce", border: "#e9d5ff" },
  pending: { bg: "#f3f4f6", text: "#9ca3af", border: "#e5e7eb" },
};

export const STATUS_LABEL = {
  correct: "✓ correct",
  wrong: "✗ wrong",
  "no-answer": "○ no answer",
  timeout: "⏱ timeout",
  "token-cap": "⊘ token cap",
  "provider-error": "⚡ provider error",
  pending: "· pending",
};

export const ERROR_LABELS = {
  "wrong-class": "resolved the wrong class/subject",
  "wrong-value": "right class, wrong number",
  "wrong-uncertainty": "value ok, ± is off",
  "no-answer": "no parseable answer produced",
  "not-grounded": "answered without running a SPARQL query",
  "wrong-shape": "answer shape can't be scored",
  "provider-error": "LLM provider failed the call",
  timeout: "wall-clock deadline exceeded",
  "token-cap": "token budget exhausted",
};

// Group cases by case_id so we can show fq + composition side-by-side
export function groupCases(cases) {
  const map = new Map();
  for (const c of cases) {
    const id = c.case_id;
    if (!map.has(id)) map.set(id, { case_id: id, question: c.question, expected: c.expected, backends: {} });
    map.get(id).backends[c.backend] = c;
  }
  return [...map.values()];
}

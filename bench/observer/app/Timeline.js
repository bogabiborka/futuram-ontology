"use client";
import { StepPill, Legend } from "./Legend.js";
import { classifyTool } from "./format.js";

// ── data model ──────────────────────────────────────────────────────────────

// Extract steps (queries / searches / skill reads) from one snapshot's messages.
function stepsOf(msgs) {
  const steps = [];
  for (const m of msgs) {
    if (m.role === "assistant") {
      for (const c of m.tool_calls || []) {
        if (c.name === "execute_sparql_query") steps.push({ type: "q", arg: c.arguments?.sparql_query });
        else if (c.name === "search_sparql_docs") steps.push({ type: "search" });
        else if (c.name === "list_skills") steps.push({ type: "skills" });
        else if (c.name === "get_skill") steps.push({ type: "skill", arg: c.arguments?.skill_id });
      }
    } else if (m.role === "tool") {
      const last = steps[steps.length - 1];
      if (last && last.pending == null) {
        last.kind = classifyTool(m.content);
        last.helper = (m.content || "").includes("[helper]");
        last.next = (m.content || "").includes("[next]") || (m.content || "").includes("[diagnose]");
        last.result = m.content || "";
      }
    }
  }
  return steps;
}

// The last assistant message containing an ANSWER: line (pure reasoning turns excluded).
function handedInOf(msgs) {
  for (let i = (msgs || []).length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.role === "assistant" && (m.content || "").includes("ANSWER:")) return m.content.trim();
  }
  return "";
}

// The rejection reason injected into the user message for a corrective re-prompt.
// Returns "" for the first (un-retried) round.
function repromptReasonOf(msgs) {
  const user = (msgs || []).find((m) => m.role === "user");
  if (!user) return "";
  const match = (user.content || "").match(/\[Retry[^\]]*\]\s*(.*)/s);
  if (!match) return "";
  // Truncate to first sentence so it fits on one line; full text available on hover.
  return match[1].replace(/\n.*/s, "").replace(/\.\s+.*/, ".").trim();
}

// Parse the conversation snapshot array into per-attempt groups of rounds.
// Each round = one corrective window (_attempt() call in agent.py) with its own
// steps, handed-in answer, and the rejection reason that triggered it.
export function parseAttempts(conversation) {
  const byNum = new Map();
  (conversation || []).forEach((a, ai) => {
    const n = a?.attempt ?? ai + 1;
    const msgs = a?.messages || [];
    const rounds = byNum.get(n) || [];
    rounds.push({
      steps: stepsOf(msgs),
      ans: handedInOf(msgs),
      reprompt: repromptReasonOf(msgs),
    });
    byNum.set(n, rounds);
  });
  return [...byNum.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([n, rounds]) => ({ n, rounds }))
    .filter((g) => g.rounds.some((r) => r.steps.length || r.ans));
}

// ── rendering ────────────────────────────────────────────────────────────────

function StepPills({ steps, offset }) {
  return steps.map((s, i) => {
    const n = offset + i + 1;
    if (s.type === "search") return <StepPill key={i} kind="search" n={n} result={s.result} />;
    if (s.type === "skills") return <StepPill key={i} kind="skills" n={n} result={s.result} />;
    if (s.type === "skill") return <StepPill key={i} kind="skill" n={n} sub={s.arg || "skill"} result={s.result} />;
    const kind = s.kind === "data" ? "data" : s.kind === "invalid" ? "invalid" : "empty";
    return <StepPill key={i} kind={kind} n={n} query={s.arg || "(no query text captured)"} result={s.result} />;
  });
}

// One corrective round: [rejection reason] → steps → [answer pill].
// Rejection reason is omitted for the first round (nothing rejected it yet).
function RoundRow({ round, stepOffset, answerIndex, totalAnswers }) {
  const { steps, ans, reprompt } = round;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {reprompt && (
        <span className="font-mono text-[10px] text-destructive/80 max-w-[32rem] truncate shrink-0"
          title={reprompt}>
          ↳ {reprompt}
        </span>
      )}
      <StepPills steps={steps} offset={stepOffset} />
      {ans && (
        <StepPill kind="answer"
          sub={totalAnswers > 1 ? `answer ${answerIndex}` : "answer"}
          result={ans} />
      )}
    </div>
  );
}

// Timeline for one attempt: rounds in chronological order.
function AttemptTimeline({ rounds }) {
  let stepOffset = 0;
  let answerIndex = 0;
  const totalAnswers = rounds.filter((r) => r.ans).length;
  return (
    <div className="space-y-1">
      {rounds.map((r, ri) => {
        const row = (
          <RoundRow key={ri} round={r} stepOffset={stepOffset}
            answerIndex={r.ans ? ++answerIndex : answerIndex}
            totalAnswers={totalAnswers} />
        );
        stepOffset += r.steps.length;
        return row;
      })}
    </div>
  );
}

// Full timeline across all attempts. Single-attempt runs use a compact header;
// multi-attempt runs label each attempt block separately.
export function QueryTimeline({ attempts }) {
  const groups = parseAttempts(attempts);
  if (!groups.length) return null;
  const legend = (
    <Legend trigger={
      <button className="ml-0.5 font-mono text-[10px] text-muted-foreground underline underline-offset-2 hover:text-foreground">
        legend
      </button>
    } />
  );
  if (groups.length === 1) {
    return (
      <div className="space-y-1">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">steps</span>
          {legend}
        </div>
        <AttemptTimeline rounds={groups[0].rounds} />
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {groups.map((g, gi) => (
        <div key={gi}>
          <div className="flex items-center gap-1.5 mb-1">
            <span className="inline-flex items-center rounded-sm border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground shrink-0">
              attempt {g.n}
            </span>
            {gi === groups.length - 1 && legend}
          </div>
          <AttemptTimeline rounds={g.rounds} />
        </div>
      ))}
    </div>
  );
}

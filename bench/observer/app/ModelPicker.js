"use client";
import { useEffect, useState } from "react";
import { Loader2, Infinity as InfinityIcon } from "lucide-react";

// Live Copilot budget chip — fetches /api/quota and renders the account's plan +
// remaining quotas, so the user can SEE whether a model has budget before running
// (the dropdown lists names only; this answers "do I still have budget?"). The
// "chat"/"completions" (utility/base) quotas are usually `unlimited` — the rate
// limit on those is a per-minute throttle, NOT a budget; `premium_interactions`
// is the spendable monthly bucket. Renders nothing unless provider is copilot.
export function CopilotQuotaChip({ provider }) {
  const [q, setQ] = useState(undefined); // undefined=loading, null=unavailable
  useEffect(() => {
    if (provider !== "copilot") { setQ(null); return; }
    let cancelled = false;
    setQ(undefined);
    fetch("/api/quota")
      .then((r) => r.json())
      .then((j) => { if (!cancelled) setQ(j && j.quotas ? j : null); })
      .catch(() => { if (!cancelled) setQ(null); });
    return () => { cancelled = true; };
  }, [provider]);

  if (provider !== "copilot") return null;
  if (q === undefined) {
    return (
      <div className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
        <Loader2 className="size-3 animate-spin" /> checking Copilot budget…
      </div>
    );
  }
  if (!q) return null;
  const utility = q.quotas?.chat?.unlimited || q.quotas?.completions?.unlimited;
  const prem = q.quotas?.premium_interactions;
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 font-mono text-[11px] text-muted-foreground">
      <span className="uppercase tracking-wider text-foreground/70">{q.plan || "copilot"} budget</span>
      {utility && (
        <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
          <InfinityIcon className="size-3" /> base/utility models (gpt-4o etc.): unlimited
        </span>
      )}
      {prem && (
        <span className={prem.percent_remaining <= 10 ? "text-destructive font-semibold" : ""}>
          premium {prem.remaining}/{prem.entitlement} ({Math.round(prem.percent_remaining)}%)
        </span>
      )}
      {q.quota_reset_date && <span>resets {q.quota_reset_date}</span>}
    </div>
  );
}

// A model dropdown fed by /api/models?provider=<name> (live list from the
// provider — Copilot/OpenAI/Groq/OpenRouter via /v1/models, Ollama via its
// daemon, Anthropic/Gemini via their list endpoints). Falls back to a free-text
// input when the provider can't list (no key, or nothing returned). `value`/
// `onChange` are the model string; empty = the profile's default.
const selectCls =
  "h-8 w-full rounded-md border border-input bg-transparent px-2 font-mono text-xs shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

export function ModelPicker({ provider, value, onChange, placeholder }) {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!provider) { setModels([]); return; }
    let cancelled = false;
    setLoading(true);
    fetch(`/api/models?provider=${encodeURIComponent(provider)}`)
      .then((r) => r.json())
      .then((j) => { if (!cancelled) setModels(j.models || []); })
      .catch(() => { if (!cancelled) setModels([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [provider]);

  if (loading) {
    return (
      <div className={selectCls + " flex items-center gap-1.5 text-muted-foreground"}>
        <Loader2 className="size-3 animate-spin" /> loading models…
      </div>
    );
  }

  // No list available → free text (still lets the user type any model/tag).
  if (!models.length) {
    return (
      <input value={value || ""} onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder || "model (type a tag)"} className={selectCls} />
    );
  }

  // Dropdown. Include the current value even if not in the list (so a saved
  // custom model still shows), plus a "(profile default)" empty option.
  const opts = value && !models.includes(value) ? [value, ...models] : models;
  return (
    <select value={value || ""} onChange={(e) => onChange(e.target.value)} className={selectCls}>
      <option value="">(profile default)</option>
      {opts.map((m) => <option key={m} value={m}>{m}</option>)}
    </select>
  );
}

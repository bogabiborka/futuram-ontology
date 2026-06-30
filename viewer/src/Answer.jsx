import { fmtNum, localName } from "./format.js";
import { cn } from "./utils.js";

const _num = (x) => (x == null || x === "" ? null : Number(x));

export function answerToRows(ans) {
  if (!ans || typeof ans !== "object") return { rows: [], unit: "" };
  if (Array.isArray(ans.names))
    return { rows: ans.names.map((n) => ({ label: String(n), value: null, unc: null })), unit: "" };
  const unit = ans.unit || "";
  if (ans.value != null && ans.values == null)
    return { rows: [{ label: ans.label || "", value: Number(ans.value), unc: _num(ans.uncertainty) }], unit };
  let labels = Array.isArray(ans.labels) ? ans.labels.map(String) : [];
  let vals = [];
  let uncByLabel = null, uncArr = null;
  if (ans.values && !Array.isArray(ans.values) && typeof ans.values === "object") {
    labels = Object.keys(ans.values);
    vals = Object.values(ans.values).map(Number);
  } else {
    vals = Array.isArray(ans.values) ? ans.values.map(Number) : [];
  }
  const u = ans.uncertainties ?? ans.uncertainty;
  if (u && !Array.isArray(u) && typeof u === "object") uncByLabel = u;
  else if (Array.isArray(u)) uncArr = u.map(_num);
  if (!vals.length && labels.length)
    return { rows: labels.map((n) => ({ label: String(n), value: null, unc: null })), unit: "" };
  return {
    rows: vals.map((v, i) => {
      const label = labels[i] ?? "";
      const unc = uncByLabel ? _num(uncByLabel[label]) : uncArr ? (uncArr[i] ?? null) : null;
      return { label, value: v, unc };
    }),
    unit,
  };
}

const _norm = (s) => {
  let t = String(s || "").trim();
  if (t.startsWith("<") && t.endsWith(">")) t = t.slice(1, -1);
  return (t.split(/[#/]/).pop() || "").split(":").pop().toLowerCase().replace(/[\s_-]/g, "");
};

const _close = (a, b) => {
  if (a == null || b == null) return a === b;
  const A = Number(a), B = Number(b);
  if (!isFinite(A) || !isFinite(B)) return false;
  return (
    Math.abs(A - B) <= Math.max(Math.max(Math.abs(A), Math.abs(B)) * 0.02, 1e-9) ||
    Number(A.toPrecision(2)) === Number(B.toPrecision(2))
  );
};

const MARK = { match: "✓", off: "≠", extra: "+", missing: "−" };
const MARK_CLS = {
  match: "text-ok font-bold",
  off: "text-warn font-bold",
  extra: "text-blue font-bold",
  missing: "text-bad font-bold",
};

export function DiffView({ got, expected }) {
  const g = answerToRows(got);
  const e = answerToRows(expected);
  if (!e.rows.length) return <AnswerView ans={got} />;
  const showUnc = g.rows.some((r) => r.unc != null) || e.rows.some((r) => r.unc != null);
  const matched = new Set();
  const rows = e.rows.map((er) => {
    const gi = g.rows.findIndex((gr, i) => !matched.has(i) && _norm(gr.label) === _norm(er.label));
    let state = "missing", gr = null;
    if (gi >= 0) {
      matched.add(gi);
      gr = g.rows[gi];
      state = er.value == null && gr.value == null ? "match" : _close(gr.value, er.value) ? "match" : "off";
    }
    return { er, gr, state };
  });
  const extras = g.rows.filter((_, i) => !matched.has(i));

  return (
    <table className="w-full border-collapse text-xs">
      <thead>
        <tr className="border-b border-border">
          <th className="text-left pb-1.5 pr-2 text-muted-foreground font-medium">constituent</th>
          <th className="text-right pb-1.5 px-2 text-muted-foreground font-medium">expected</th>
          <th className="text-right pb-1.5 px-2 text-muted-foreground font-medium">got</th>
          {showUnc && <th className="text-right pb-1.5 pl-2 text-muted-foreground font-medium">± got</th>}
          <th className="w-5 pb-1.5" />
        </tr>
      </thead>
      <tbody>
        {rows.map(({ er, gr, state }, i) => (
          <tr key={i} className="border-b border-border/30">
            <td className="py-0.5 pr-2 font-mono text-[11px]">{localName(er.label) || "—"}</td>
            <td className="py-0.5 px-2 text-right tabular-nums">
              {er.value != null ? fmtNum(er.value) : "—"}
              {er.unc != null && <span className="text-muted-foreground"> ±{fmtNum(er.unc)}</span>}
              {e.unit && <span className="text-muted-foreground ml-1 text-[10px]">{e.unit}</span>}
            </td>
            <td className="py-0.5 px-2 text-right tabular-nums">
              {gr ? (gr.value != null ? fmtNum(gr.value) : "—") : "—"}
            </td>
            {showUnc && (
              <td className="py-0.5 pl-2 text-right tabular-nums text-muted-foreground">
                {gr?.unc != null ? `±${fmtNum(gr.unc)}` : "—"}
              </td>
            )}
            <td className={cn("py-0.5 pl-2", MARK_CLS[state])}>{MARK[state]}</td>
          </tr>
        ))}
        {extras.map((gr, i) => (
          <tr key={`x${i}`} className="border-b border-border/30 opacity-60">
            <td className="py-0.5 pr-2 font-mono text-[11px]">{localName(gr.label) || "—"}</td>
            <td className="py-0.5 px-2 text-right text-muted-foreground">—</td>
            <td className="py-0.5 px-2 text-right tabular-nums">
              {gr.value != null ? fmtNum(gr.value) : "—"}
            </td>
            {showUnc && <td className="py-0.5 pl-2 text-right text-muted-foreground">{gr.unc != null ? `±${fmtNum(gr.unc)}` : "—"}</td>}
            <td className={cn("py-0.5 pl-2", MARK_CLS.extra)}>{MARK.extra}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function AnswerView({ ans }) {
  const { rows, unit } = answerToRows(ans);
  if (!rows.length) return <span className="text-muted-foreground text-xs">no answer</span>;
  return (
    <div className="text-xs space-y-0.5">
      {rows.map((r, i) => (
        <div key={i} className="tabular-nums">
          {r.label && <span className="font-mono text-[11px] text-muted-foreground mr-1.5">{localName(r.label)}</span>}
          {r.value != null ? fmtNum(r.value) : "—"}
          {r.unc != null && <span className="text-muted-foreground"> ±{fmtNum(r.unc)}</span>}
          {unit && <span className="text-muted-foreground ml-1">{unit}</span>}
        </div>
      ))}
    </div>
  );
}

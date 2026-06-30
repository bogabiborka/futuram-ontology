"use client";
import { useState, useEffect } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import {
  Table as T,
  TableBody as TBody,
  TableCell as TD,
  TableFooter as TFoot,
  TableRow as TR,
} from "@/components/ui/table";

// Normalise any answer shape the harness/model emits into rows of {label, value,
// unc}. Accepts {values:[], labels:[], uncertainties:[], unit} | {value,
// uncertainty, unit} | {names:[]} (membership), and the expected-side shapes
// {values:{label:v}, uncertainty:{label:u}} (a dict keyed by label). `unc` is the
// ± uncertainty for that row (null when none given).
const _numOrNull = (x) => (x == null || x === "" ? null : Number(x));
export function answerToRows(ans) {
  if (!ans || typeof ans !== "object") return { rows: [], unit: "", names: null };
  if (Array.isArray(ans.names)) {
    return { rows: ans.names.map((n) => ({ label: String(n), value: null, unc: null })), unit: "", names: ans.names };
  }
  const unit = ans.unit || "";
  // scalar single-value answer (model: {value, uncertainty}; expected: same)
  if (ans.value != null && ans.values == null) {
    return { rows: [{ label: ans.label || "", value: Number(ans.value), unc: _numOrNull(ans.uncertainty) }], unit, names: null };
  }
  // values may be an ARRAY (model answer) or an OBJECT map (expected ground truth)
  let labels = Array.isArray(ans.labels) ? ans.labels.map(String) : [];
  let vals = [];
  let uncByLabel = null, uncArr = null;
  if (ans.values && !Array.isArray(ans.values) && typeof ans.values === "object") {
    // {label: value} map — the expected ground-truth shape
    labels = Object.keys(ans.values);
    vals = Object.values(ans.values).map(Number);
  } else {
    vals = Array.isArray(ans.values) ? ans.values.map(Number) : [];
  }
  // uncertainty: a {label: ±} map OR an array aligned with values
  const u = ans.uncertainties ?? ans.uncertainty;
  if (u && !Array.isArray(u) && typeof u === "object") uncByLabel = u;
  else if (Array.isArray(u)) uncArr = u.map(_numOrNull);
  // names-only answer flattened into {values:[], labels:[...]} -> membership
  if (!vals.length && labels.length) {
    return { rows: labels.map((n) => ({ label: String(n), value: null, unc: null })), unit: "", names: labels };
  }
  const rows = vals.map((v, i) => {
    const label = labels[i] ?? "";
    const unc = uncByLabel ? _numOrNull(uncByLabel[label]) : (uncArr ? (uncArr[i] ?? null) : null);
    return { label, value: v, unc };
  });
  return { rows, unit, names: null };
}

// ---- diff view: model answer vs expected, by label ---- //
// Compare labels by class identity, not raw string: a prefixed IRI (futuram:Iron)
// and the full IRI (https://www.purl.org/futuram#Iron) denote the same resource.
// Collapse both to the local name (after the last #, /, or prefix ":") before
// comparing, matching how the scorer determines identity.
const _norm = (s) => {
  let t = String(s || "").trim();
  if (t.startsWith("<") && t.endsWith(">")) t = t.slice(1, -1);
  const local = t.split(/[#/]/).pop();           // strip namespace IRI
  const tail = (local || t).split(":").pop();     // strip prefix (futuram:)
  return (tail || "").toLowerCase().replace(/[\s_-]/g, "");
};
// Tolerances mirror scoring.py score_answer() exactly — one place to update both.
const SCORE_TOL = { value: { rtol: 0.02, atol: 1e-9 }, unc: { rtol: 0.20, atol: 0.05 } };
function _close(a, b, kind = "value") {
  if (a == null || b == null) return a == b;
  const A = Number(a), B = Number(b);
  if (!isFinite(A) || !isFinite(B)) return false;
  const { rtol, atol } = SCORE_TOL[kind];
  const tol = Math.max(Math.abs(A), Math.abs(B)) * rtol;
  return Math.abs(A - B) <= Math.max(tol, atol) ||
    Number(A.toPrecision(2)) === Number(B.toPrecision(2));
}

const fmtNum = (n) => Number(n).toLocaleString(undefined, { maximumFractionDigits: 6 });

const STATE_MARK = { match: "✓", off: "≠", extra: "+", missing: "−" };
const STATE_CLR = {
  match: "text-ok",
  off: "text-warn",
  extra: "text-blue",
  missing: "text-bad",
};

export function DiffView({ got, expected }) {
  const g = answerToRows(got);
  const e = answerToRows(expected);
  if (!e.rows.length) {
    // no expected to diff against — just show the answer
    return <AnswerView ans={got} />;
  }
  // Show the ± column whenever EITHER side carries an uncertainty: the EXPECTED
  // side (a __valunc case, where the ± is scored) OR the model's own answer (it
  // volunteered a ± even though the case didn't demand one — still worth seeing).
  const hasUnc = e.rows.some((r) => r.unc != null) || g.rows.some((r) => r.unc != null);

  // SCALAR case: single expected value (labelled or not) and model answered with
  // one unlabelled scalar — show a clean got-vs-expected line without the label diff.
  const gotIsScalar = !g.names && g.rows.length === 1 && !g.rows[0]?.label;
  if (!e.names && e.rows.length === 1 && (!e.rows[0].label || gotIsScalar)) {
    const er = e.rows[0];
    const gr = g.rows[0];
    const valOk = gr && _close(gr.value, er.value, "value");
    const uncOk = er.unc == null || (gr && _close(gr.unc, er.unc, "unc"));
    const ok = !!(valOk && uncOk);
    return (
      <div className="space-y-2">
        <Badge variant={ok ? "default" : "outline"} className="rounded-sm font-mono">
          {ok ? "match" : "no match"}
        </Badge>
        <div className={cn("rounded-md border px-3 py-2 flex items-baseline justify-between gap-3",
          ok ? "border-ok/50" : "border-bad/50")}>
          <span className={cn("font-mono text-sm font-semibold", ok ? "text-ok" : "text-bad")}>
            {ok ? "✓" : "✗"}{" "}
            <span className="text-foreground">
              {gr ? fmtNum(gr.value) : "—"}
              {hasUnc && gr && gr.unc != null && <span className="text-muted-foreground"> ± {fmtNum(gr.unc)}</span>}
            </span>
          </span>
          <span className="font-mono text-xs text-muted-foreground">
            expected {fmtNum(er.value)}{er.unc != null && ` ± ${fmtNum(er.unc)}`} {e.unit}
          </span>
        </div>
      </div>
    );
  }

  const gByLabel = new Map(g.rows.map((r) => [_norm(r.label), r]));
  const seen = new Set();
  const rows = e.rows.map((er) => {
    const gr = gByLabel.get(_norm(er.label));
    seen.add(_norm(er.label));
    let state = "missing";
    if (gr) {
      const valOk = e.names || _close(gr.value, er.value, "value");
      // when the expected carries an uncertainty, it must ALSO match (mirrors the
      // run_bench __valunc gate); a right value with a wrong/absent ± is "off".
      const uncOk = er.unc == null || _close(gr.unc, er.unc, "unc");
      state = valOk && uncOk ? "match" : "off";
    }
    return { label: er.label, exp: er.value, got: gr ? gr.value : null,
             expUnc: er.unc, gotUnc: gr ? gr.unc : null, state };
  });
  const extras = g.rows.filter((r) => !seen.has(_norm(r.label)))
    .map((r) => ({ label: r.label, exp: null, got: r.value, expUnc: null, gotUnc: r.unc, state: "extra" }));
  const all = [...rows, ...extras];
  const nMatch = all.filter((r) => r.state === "match").length;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
        <Badge variant={nMatch === e.rows.length ? "default" : "outline"} className="rounded-sm font-mono">
          {nMatch}/{e.rows.length} match
        </Badge>
        {extras.length > 0 && <span className="text-blue">+{extras.length} extra</span>}
      </div>
      <div className="rounded-md border">
        <T>
          <TBody>
            {all.map((r, i) => (
              <TR key={i} className="border-border/60">
                <TD className={cn("w-6 text-center font-mono font-semibold", STATE_CLR[r.state])}>
                  {STATE_MARK[r.state]}
                </TD>
                <TD className="font-mono text-xs break-all">
                  {r.label || <span className="text-muted-foreground italic">missing class IRI</span>}
                </TD>
                {!e.names && (
                  <TD className="text-right font-mono text-xs whitespace-nowrap">
                    {r.got != null ? fmtNum(r.got) : <span className="text-muted-foreground">—</span>}
                    {hasUnc && r.gotUnc != null && (
                      <span className="text-muted-foreground"> ± {fmtNum(r.gotUnc)}</span>
                    )}
                    {r.state === "off" && (
                      <span className="text-warn"> (exp {fmtNum(r.exp)}
                        {r.expUnc != null && ` ± ${fmtNum(r.expUnc)}`})</span>
                    )}
                  </TD>
                )}
              </TR>
            ))}
          </TBody>
        </T>
      </div>
    </div>
  );
}

// ---- read-only view ---- //
export function AnswerView({ ans, correct }) {
  const { rows, unit, names } = answerToRows(ans);
  if (!rows.length) return <div className="text-sm text-muted-foreground italic">— no answer —</div>;
  const border = cn("rounded-md border",
    correct === true && "border-ok/60", correct === false && "border-bad/60");

  // routes may be:
  //   - a dict {elementIRI: processIRI}  (model answer shape, single route per element)
  //   - an array [{element, base_metal, route}]  (expected route_rows shape, multi-route)
  const _rawRoutes = ans && typeof ans === "object" ? ans.routes : null;
  const routesDict = (_rawRoutes && !Array.isArray(_rawRoutes) && typeof _rawRoutes === "object")
    ? _rawRoutes : null;
  const routeRows = Array.isArray(_rawRoutes) ? _rawRoutes : null;
  const routes = routesDict || routeRows ? _rawRoutes : null;
  const _short = (iri) => String(iri).split(/[#/]/).pop();

  // SCALAR answer (a single value with no class label — a total/sum): show the
  // number cleanly on one line, no "(unnamed)" placeholder, no redundant total row.
  if (!names && rows.length === 1 && !rows[0].label) {
    const r = rows[0];
    return (
      <div className={cn(border, "px-3 py-2 flex items-baseline justify-between gap-3")}>
        <span className="font-mono text-xs text-muted-foreground">value</span>
        <span className="font-mono text-sm">
          <b>{fmtNum(r.value)}</b>
          {r.unc != null && <span className="text-muted-foreground"> ± {fmtNum(r.unc)}</span>}
          <span className="text-muted-foreground"> {unit}</span>
        </span>
      </div>
    );
  }

  // NO synthesized "total" row: a labelled list is a per-class breakdown, a ranking,
  // or a distribution — summing its rows is meaningless (and the scorer never sums
  // them; it matches per label). A genuine single-total case is the scalar branch
  // above, which already shows its one value. So we only ever list the rows here.
  return (
    <div className="space-y-1">
      <div className={border}>
        <T>
          <TBody>
            {rows.map((r, i) => (
              <TR key={i} className="border-border/60">
                <TD className="font-mono text-xs break-all">
                  {r.label || <span className="text-muted-foreground">item {i + 1}</span>}
                </TD>
                {!names && (
                  <TD className="text-right font-mono text-xs whitespace-nowrap">
                    {fmtNum(r.value)}
                    {r.unc != null && <span className="text-muted-foreground"> ± {fmtNum(r.unc)}</span>}
                    <span className="text-muted-foreground"> {unit}</span>
                  </TD>
                )}
                {routesDict && (
                  <TD className="text-right font-mono text-xs text-muted-foreground whitespace-nowrap">
                    {routesDict[r.label] ? _short(routesDict[r.label]) : "—"}
                  </TD>
                )}
              </TR>
            ))}
          </TBody>
        </T>
      </div>
      {routesDict && (
        <div className="font-mono text-xs text-muted-foreground px-1">
          routes: {Object.entries(routesDict).map(([k, v]) => `${_short(k)}→${_short(v)}`).join(", ")}
        </div>
      )}
      {routeRows && (
        <div className="font-mono text-xs text-muted-foreground px-1 space-y-0.5">
          {routeRows.map((row, i) => (
            <div key={i}>
              {_short(row.element)} via {_short(row.base_metal)} → {_short(row.route)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- shared editable rows (used by the inline editor + the case set/edit) ---- //
// Build the expected payload ({names:[]} | {unit, values:{}}) from editor state.
export function rowsToExpected(rows, unit, names) {
  return names
    ? { names: rows.map((r) => r.label).filter(Boolean) }
    : { unit, values: Object.fromEntries(rows.filter((r) => r.label).map((r) => [r.label, Number(r.value)])) };
}

function ExpectedRows({ rows, setRows, unit, setUnit, names, setNames }) {
  const upd = (i, k, v) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const add = () => setRows((rs) => [...rs, { label: "", value: 0 }]);
  const del = (i) => setRows((rs) => rs.filter((_, j) => j !== i));

  const pasteJson = (text) => {
    try {
      const m = text.match(/ANSWER:\s*(\{.*\}|\[.*\])/s);
      const obj = JSON.parse(m ? m[1] : text);
      const n = answerToRows(obj);
      if (n.rows.length) { setRows(n.rows); setUnit(n.unit || unit); setNames(!!n.names); }
    } catch {}
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Label className="flex items-center gap-1.5 font-mono text-xs cursor-pointer">
          <input type="checkbox" checked={names} onChange={(e) => setNames(e.target.checked)} className="accent-primary" />
          names-only
        </Label>
        {!names && (
          <span className="flex items-center gap-1.5 font-mono text-xs">
            unit
            <Input value={unit} onChange={(e) => setUnit(e.target.value)} className="h-7 w-16 font-mono text-xs" />
          </span>
        )}
        <Button variant="outline" size="sm" className="h-7 font-mono text-xs" onClick={() => {
          const t = prompt("Paste the model's ANSWER line or JSON:");
          if (t) pasteJson(t);
        }}>paste JSON</Button>
      </div>

      <div className="rounded-md border">
        <T>
          <TBody>
            {rows.map((r, i) => (
              <TR key={i} className="border-border/60">
                <TD className="py-1.5">
                  <Input value={r.label} placeholder="class IRI (futuram:Iron) / label"
                    onChange={(e) => upd(i, "label", e.target.value)}
                    className="h-7 font-mono text-xs" />
                </TD>
                {!names && (
                  <TD className="py-1.5 w-32">
                    <Input type="number" step="any" value={r.value}
                      onChange={(e) => upd(i, "value", e.target.value === "" ? "" : Number(e.target.value))}
                      className="h-7 font-mono text-xs text-right" />
                  </TD>
                )}
                <TD className="py-1.5 w-8">
                  <Button variant="ghost" size="icon" className="h-7 w-7 text-bad" onClick={() => del(i)}>×</Button>
                </TD>
              </TR>
            ))}
          </TBody>
        </T>
      </div>

      <Button variant="outline" size="sm" className="h-7 font-mono text-xs" onClick={add}>+ row</Button>
    </div>
  );
}

// Hook owning editor row-state, seeded from an initial answer object.
function useExpectedState(initial) {
  const init = answerToRows(initial);
  const [rows, setRows] = useState(init.rows.length ? init.rows : [{ label: "", value: 0 }]);
  const [unit, setUnit] = useState(init.unit || "kg");
  const [names, setNames] = useState(!!init.names);
  useEffect(() => {
    const n = answerToRows(initial);
    if (n.rows.length) { setRows(n.rows); setUnit(n.unit || "kg"); setNames(!!n.names); }
  }, [initial]);
  return { rows, setRows, unit, setUnit, names, setNames };
}

// ---- controlled inline editor: emits the expected object via onChange ---- //
// No save button, no API call — used inside the combined Ask panel so the
// expected travels WITH the run (never written to the library as an orphan).
export function InlineExpectedEditor({ value, onChange }) {
  const st = useExpectedState(value);
  const { rows, unit, names } = st;
  // bubble the built expected up whenever the editable state changes
  useEffect(() => {
    const hasContent = names
      ? rows.some((r) => r.label.trim())
      : rows.some((r) => r.label.trim() && r.value !== "" && r.value != null);
    onChange(hasContent ? rowsToExpected(rows, unit, names) : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(rows), unit, names]);
  return <ExpectedRows {...st} />;
}

// ---- case set/edit: writes the expected into the testcases YAML ---- //
export function AnswerEditor({ caseId, initial, onSaved }) {
  const st = useExpectedState(initial);
  const { rows, unit, names } = st;
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const save = async () => {
    setSaving(true); setMsg("");
    try {
      const expected = rowsToExpected(rows, unit, names);
      const r = await fetch("/api/expected", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ case_id: caseId, expected }),
      });
      const j = await r.json();
      setMsg(r.ok ? "saved to testcases ✓" : (j.error || "save failed"));
      if (r.ok && onSaved) onSaved(expected);
    } catch (e) { setMsg(String(e)); }
    finally { setSaving(false); }
  };

  return (
    <div className="space-y-3">
      <ExpectedRows {...st} />
      <div className="flex items-center gap-2">
        <Button size="sm" className="h-7 font-mono text-xs" onClick={save} disabled={saving}>
          {saving ? "saving…" : "Save as expected"}
        </Button>
        {msg && <span className="font-mono text-xs text-muted-foreground">{msg}</span>}
      </div>
    </div>
  );
}

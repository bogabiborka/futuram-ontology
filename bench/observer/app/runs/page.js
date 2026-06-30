"use client";
import { useEffect, useState, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Scoreboard, CaseRow } from "../Sidebar.js";
import { CaseDetail } from "../CaseDetail.js";
import { TopBar } from "../TopBar.js";

const POLL_MS = 2500;

// The experiment view (the head-to-head fq-vs-composition run browser). Lives at
// /runs; the homepage (/) is the landing + provider login page.
export default function RunsPage() {
  const [runs, setRuns] = useState([]);
  const [selCase, setSelCase] = useState(null);
  const [details, setDetails] = useState({});
  const [live, setLive] = useState(false);
  const [compare, setCompare] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  // optional ?dir= — stream from a specific experiment run folder instead of the
  // shared live dir. Read from the URL on mount (avoids the useSearchParams
  // Suspense requirement); appended to every runs/detail fetch.
  const [dir, setDir] = useState(null);
  useEffect(() => {
    try { setDir(new URLSearchParams(window.location.search).get("dir") || null); } catch {}
  }, []);
  const qs = dir ? `?dir=${encodeURIComponent(dir)}` : "";

  const poll = useCallback(async () => {
    try {
      const r = await fetch(`/api/runs${qs}`, { cache: "no-store" });
      const j = await r.json();
      setRuns(j.runs || []);
      setLive((j.runs || []).some((x) => x.status === "running"));
    } catch {}
  }, [qs]);

  useEffect(() => {
    poll();
    const t = setInterval(poll, POLL_MS);
    return () => clearInterval(t);
  }, [poll]);

  const byCase = {};
  const caseOrder = [];
  for (const r of runs) {
    if (!byCase[r.case_id]) { byCase[r.case_id] = {}; caseOrder.push(r.case_id); }
    byCase[r.case_id][r.backend] = r;
  }

  const selRuns = selCase ? byCase[selCase] : null;
  // Clear cached per-run details the instant the SELECTED case changes, so the
  // detail pane (and the expected box) never shows the PREVIOUS case's data while
  // this case's details are still being fetched — it falls back to this case's
  // fresh summary (which carries `expected`) immediately, not one poll later.
  useEffect(() => { setDetails({}); }, [selCase]);
  useEffect(() => {
    if (!selRuns) return;
    const ids = Object.values(selRuns).map((r) => r.id);
    const fetchAll = () =>
      ids.forEach(async (id) => {
        try {
          const r = await fetch(`/api/run/${id}${qs}`, { cache: "no-store" });
          if (r.ok) { const d = await r.json(); setDetails((p) => ({ ...p, [id]: d })); }
        } catch {}
      });
    fetchAll();
    const t = setInterval(fetchAll, POLL_MS);
    return () => clearInterval(t);
  }, [selCase, qs, selRuns && Object.values(selRuns).map((r) => r.id).join(",")]);

  useEffect(() => {
    if (!selCase && caseOrder.length) {
      const running = caseOrder.find((c) => Object.values(byCase[c]).some((r) => r.status === "running"));
      setSelCase(running || caseOrder[0]);
    }
  }, [caseOrder.length]);

  const tally = (be) => {
    // ask-mode runs have no verdict — exclude them from the scored tally (n) so
    // the scoreboard isn't "0/1" for an ad-hoc ask. Their tokens still count.
    const rs = runs.filter((r) => r.backend === be);
    const scored = rs.filter((r) => !r.askMode);
    return { ok: scored.filter((r) => r.correct === true).length, n: scored.length,
             tok: rs.reduce((a, r) => a + (r.tokens || 0), 0) };
  };
  const maxTok = Math.max(1, ...runs.map((r) => r.tokens || 0));
  const fq = tally("fq"), composition = tally("composition");

  // The left list can be fully hidden, or — in compare mode — shown as a thin
  // rail so the detail still gets most of the width. When hidden, the detail
  // takes the whole screen.
  const railWidth = compare ? "220px" : "360px";
  const fullWidth = compare || !sidebarOpen;

  // run_meta (model + params) of the currently selected case — read from either
  // backend's detail or its list summary. Drives the TopBar's model/params display
  // and the in-place model switcher.
  const runMeta = (() => {
    if (!selRuns) return null;
    for (const r of Object.values(selRuns)) {
      const m = details[r.id]?.run_meta || r.run_meta;
      if (m) return m;
    }
    return null;
  })();
  // the experiment this view is scoped to (when streaming an experiment folder):
  // ?dir=bench/experiments/<name>/runs
  const experiment = (() => {
    const m = /bench\/experiments\/([^/]+)\/runs/.exec(dir || "");
    return m ? m[1] : null;
  })();
  // the selected case's question + expected + backends, so an AD-HOC run (no
  // experiment) can be re-issued via /api/ask on a different model.
  const selRunInfo = (() => {
    if (!selRuns) return null;
    const rs = Object.values(selRuns);
    const r0 = rs.find((r) => details[r.id]?.question) || rs[0];
    const d = details[r0?.id] || r0 || {};
    return {
      question: d.question || r0?.question || null,
      expected: d.expected || r0?.expected || null,
      backends: rs.map((r) => r.backend).filter(Boolean).join(",") || "fq",
    };
  })();

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <TopBar
        live={live} fq={fq} composition={composition}
        compare={compare} onToggleCompare={() => setCompare((v) => !v)}
        sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen((v) => !v)}
        runMeta={runMeta} experiment={experiment} onRelaunched={poll}
        adhoc={selRunInfo}
      />

      <div className="grid flex-1 overflow-hidden min-h-0"
        style={{ gridTemplateColumns: sidebarOpen ? `${railWidth} 1fr` : "1fr" }}>
        {sidebarOpen && (
          <aside className="flex flex-col border-r bg-sidebar overflow-hidden min-h-0">
            {!compare && (
              <div className="px-5 py-4 border-b">
                <Scoreboard fq={fq} composition={composition} />
              </div>
            )}
            <ScrollArea className="flex-1 min-h-0">
              <div className="p-3 space-y-2">
                {caseOrder.map((cid, i) => (
                  <CaseRow key={cid} cid={cid} runs={byCase[cid]} maxTok={maxTok}
                    n={i + 1}
                    selected={selCase === cid} onClick={() => setSelCase(cid)} />
                ))}
                {!caseOrder.length && (
                  <div className="px-2 py-8 text-center font-mono text-xs text-muted-foreground">No runs yet…</div>
                )}
              </div>
            </ScrollArea>
          </aside>
        )}

        <main className="overflow-y-auto">
          {selCase ? (
            <CaseDetail cid={selCase} runs={byCase[selCase]} details={details} onSaved={poll} compare={fullWidth} experiment={experiment} />
          ) : (
            <div className="flex h-full items-center justify-center font-mono text-sm text-muted-foreground">
              Select a case.
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

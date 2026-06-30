"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Render model/tool text as Markdown — fenced ```sparql blocks, lists, tables,
// bold, etc. become readable. Falls back to a <pre> for non-markdownish blobs
// (e.g. a raw JSON SPARQL result), which a markdown parser would mangle.
export default function Markdown({ text }) {
  const t = text || "";
  // Heuristic: a JSON result dump or a pure non-markdown blob renders better raw.
  const looksJson = /^\s*[\[{]/.test(t) && !t.includes("```") && !/^#/m.test(t);
  if (looksJson) return <pre className="md-raw">{t}</pre>;
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{t}</ReactMarkdown>
    </div>
  );
}

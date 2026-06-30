#!/usr/bin/env -S uv run --quiet python
# /// script
# requires-python = ">=3.9"
# ///
"""Turn revision/scaling-results.csv into a Word copy-paste-friendly Table 1.

A Markdown pipe-table pastes into Word as plain text, not a table. This emits two
forms that DO paste as a real Word table:
  * an HTML <table> (Word interprets pasted HTML as a table), and
  * a tab-separated block (paste, then Insert > Table > Convert Text to Table).

Reads the CSV produced by scaling_bench.py and writes:
  revision/table1.html   (open in a browser, select the table, copy -> paste into Word)
  revision/table1.tsv    (copy -> paste into Word -> Convert Text to Table, tab-delimited)

Usage:  uv run scripts/scaling_to_word.py [revision/scaling-results.csv]
"""
import csv
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "revision" / "scaling-results.csv"

# CSV column -> human header for the paper. Order = table column order.
COLS = [
    ("label",               "Dataset (Case)"),
    ("n_products",          "Products"),
    ("n_components",        "Components"),
    ("n_statements",        "Composition statements"),
    ("fq_triples",          "Served triples"),
    ("q_fq_median_ms",      "Query time, served view (ms)"),
    ("q_baseline_median_ms","Query time, raw graph (ms)"),
    ("derive_seconds",      "Derive time (s)"),
    ("peak_rss_mb",         "Peak derive RAM (MB)"),
    ("fq_comp_ratio",       "fq:/composition size"),
]

# point_id -> the label the paper uses (BEV-only = Case 1, full = Case 2).
LABELS = {
    "elv-bev":               "BEV only (Case 1)",
    "elv-bev-petrol":        "BEV+Petrol",
    "elv-bev-petrol-diesel": "BEV+Petrol+Diesel",
    "elv-full":              "Full fleet (Case 2)",
}


def fmt(col, v):
    if v is None or v == "":
        return "n/a"            # honest blank: not measured
    if col in ("n_products", "n_components", "n_statements", "fq_triples"):
        try:
            return f"{int(float(v)):,}"
        except ValueError:
            return v
    if col in ("derive_seconds", "q_fq_median_ms", "q_baseline_median_ms",
               "peak_rss_mb"):
        try:
            return f"{float(v):.1f}"
        except ValueError:
            return v
    if col == "fq_comp_ratio":
        try:
            return f"{float(v):.2f}x"
        except ValueError:
            return v
    return v


def read_rows():
    rows = []
    with open(SRC) as f:
        for line in f:
            if line.startswith("#"):
                continue
            rows.append(line)
    reader = csv.DictReader(rows)
    out = []
    for r in reader:
        r["label"] = LABELS.get(r["point_id"], r["point_id"])
        out.append(r)
    return out


def main():
    if not SRC.exists():
        sys.exit(f"no CSV at {SRC} — run scripts/scaling_bench.py first")
    rows = read_rows()
    headers = [h for _, h in COLS]

    # --- HTML (pastes into Word as a real table) ---
    h = ['<table border="1" cellspacing="0" cellpadding="4">']
    h.append("<tr>" + "".join(f"<th>{html.escape(x)}</th>" for x in headers) + "</tr>")
    for r in rows:
        cells = "".join(f"<td>{html.escape(fmt(c, r.get(c)))}</td>" for c, _ in COLS)
        h.append("<tr>" + cells + "</tr>")
    h.append("</table>")
    html_out = ROOT / "revision" / "table1.html"
    html_out.write_text(
        "<html><body>\n" + "\n".join(h) + "\n</body></html>\n")

    # --- TSV (paste -> Convert Text to Table) ---
    tsv = ["\t".join(headers)]
    for r in rows:
        tsv.append("\t".join(fmt(c, r.get(c)) for c, _ in COLS))
    tsv_out = ROOT / "revision" / "table1.tsv"
    tsv_out.write_text("\n".join(tsv) + "\n")

    # --- Markdown (human-readable results doc) ---
    md = ["# Scaling results (Table 1)", "",
          "Generated from `revision/scaling-results.csv` by `scaling_to_word.py`.",
          "Word-pasteable forms: `table1.html` (paste as table) / `table1.tsv` "
          "(paste, then Convert Text to Table).", "",
          "| " + " | ".join(headers) + " |",
          "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        md.append("| " + " | ".join(fmt(c, r.get(c)) for c, _ in COLS) + " |")
    md.append("")
    md.append("Query-time cells read `n/a` where no SPARQL endpoint was supplied "
              "for that point (size/derive/RAM are always measured).")
    md_out = ROOT / "revision" / "scaling-results.md"
    md_out.write_text("\n".join(md) + "\n")

    print(f"wrote {html_out}")
    print(f"wrote {tsv_out}")
    print(f"wrote {md_out}")
    print(f"\n{len(rows)} rows. Preview (TSV):\n")
    print("\n".join(tsv))


if __name__ == "__main__":
    main()

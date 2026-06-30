# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Gather every git-tracked Markdown file + every source-code comment into ONE
consolidated document. Python comments+docstrings via tokenize/ast, C-style
(js/jsx/mjs) and hash-comment (sh/rq/dot/yaml/toml) via string-aware scanners.

Usage:
  uv run scripts/gather_docs_and_comments.py            # -> docs_and_comments.txt
  uv run scripts/gather_docs_and_comments.py -o OUT.md  # custom path
  uv run scripts/gather_docs_and_comments.py --root .   # repo root (default: git toplevel)
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path


# Source extensions whose comments we extract, mapped to a comment dialect.
PY = "python"
C = "cstyle"      # // line, /* */ block
HASH = "hash"     # # line
COMMENT_LANG = {
    ".py": PY,
    ".js": C, ".jsx": C, ".mjs": C,
    ".sh": HASH, ".rq": HASH, ".dot": HASH, ".yaml": HASH, ".yml": HASH,
    ".toml": HASH,
}

# Directories never worth scanning even if a file slipped into git there.
SKIP_DIR_PARTS = {"node_modules", ".next", "dist", "build", ".git"}


def tracked_files(root: Path) -> list[Path]:
    """Every git-tracked file under `root`, as absolute paths. Falls back to a
    plain recursive walk if this is not a git repo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True, capture_output=True, text=True).stdout
        names = [n for n in out.split("\0") if n]
        return [root / n for n in names]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [p for p in root.rglob("*") if p.is_file()]


def _skip(path: Path, root: Path) -> bool:
    return any(part in SKIP_DIR_PARTS for part in path.relative_to(root).parts)


# --- comment extractors --------------------------------------------------
# Each returns a list of (line_number, text) so the writer can stamp every block
# with its exact source location (the user wants to "remember WHERE it came from").
def _py_comments(src: str) -> list[tuple[int, str]]:
    """Python `#` comments + every docstring (module/class/function), in source
    order. Docstrings are the bulk of this repo's prose, so they are included."""
    out: list[tuple[int, str]] = []
    # 1) hash comments via tokenize (skips '#' inside strings)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                text = tok.string.lstrip("#").strip()
                if text:
                    out.append((tok.start[0], text))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass  # partial file / syntax issue — keep whatever tokenized
    # 2) docstrings via ast
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=True)
                if doc:
                    lineno = getattr(node, "lineno", 1)
                    label = ("module docstring" if isinstance(node, ast.Module)
                             else f'docstring of {getattr(node, "name", "?")}')
                    out.append((lineno, f"[{label}]\n{doc}"))
    except SyntaxError:
        pass
    out.sort(key=lambda x: x[0])
    return out


def _strip_cstyle_noise(line: str) -> str:
    """Blank out string/regex literals in one JS line so we don't treat a `//`
    inside a string as a comment. Heuristic but good enough for comment harvest."""
    return re.sub(r'"(?:\\.|[^"\\])*"'
                  r"|'(?:\\.|[^'\\])*'"
                  r"|`(?:\\.|[^`\\])*`", '""', line)


def _cstyle_comments(src: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    in_block = False
    buf: list[str] = []
    buf_start = 0
    for ln, raw in enumerate(src.splitlines(), 1):
        if in_block:
            end = raw.find("*/")
            if end == -1:
                buf.append(raw.strip().lstrip("*").strip())
                continue
            buf.append(raw[:end].strip().lstrip("*").strip())
            txt = "\n".join(b for b in buf if b).strip()
            if txt:
                out.append((buf_start, txt))
            buf = []
            in_block = False
            raw = raw[end + 2:]
        scan = _strip_cstyle_noise(raw)
        # block start (may also have trailing // after) — find first marker
        b = scan.find("/*")
        l = scan.find("//")
        if b != -1 and (l == -1 or b < l):
            after = raw[b + 2:]
            endrel = _strip_cstyle_noise(after).find("*/")
            if endrel == -1:
                buf = [after.strip().lstrip("*").strip()]
                buf_start = ln
                in_block = True
            else:
                seg = after[:endrel].strip().lstrip("*").strip()
                if seg:
                    out.append((ln, seg))
            continue
        if l != -1:
            txt = raw[l + 2:].strip()
            if txt:
                out.append((ln, txt))
    return out


def _hash_comments(src: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for ln, raw in enumerate(src.splitlines(), 1):
        # skip a '#' inside a quoted string (string-aware blanking)
        scan = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', raw)
        h = scan.find("#")
        if h != -1:
            txt = raw[h + 1:].strip()
            if txt:
                out.append((ln, txt))
    return out


EXTRACTORS = {PY: _py_comments, C: _cstyle_comments, HASH: _hash_comments}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", type=Path, default=Path("docs_and_comments.txt"))
    ap.add_argument("--root", type=Path, default=None,
                    help="repo root to scan (default: git toplevel of cwd)")
    args = ap.parse_args(argv)

    if args.root is not None:
        root = args.root.resolve()
    else:
        try:
            top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                 check=True, capture_output=True, text=True).stdout.strip()
            root = Path(top)
        except (subprocess.CalledProcessError, FileNotFoundError):
            root = Path.cwd()

    listed = sorted(f for f in tracked_files(root) if not _skip(f, root))
    # tracked-but-deleted files (a pending `git rm`) are listed but not on disk —
    # report them rather than silently dropping (the user wants to know WHERE
    # everything came from, including what could NOT be read).
    missing = [f for f in listed
               if f.suffix.lower() in ({".md"} | set(COMMENT_LANG)) and not f.is_file()]
    files = [f for f in listed if f.is_file()]

    md_files = [f for f in files if f.suffix.lower() == ".md"]
    code_files = [f for f in files if f.suffix.lower() in COMMENT_LANG]

    parts: list[str] = []
    parts.append("# Consolidated docs + code comments\n")
    parts.append(f"Generated by scripts/gather_docs_and_comments.py over `{root}`.\n")
    parts.append(f"{len(md_files)} markdown files, {len(code_files)} commented "
                 f"source files.\n")
    parts.append("Provenance: every section is headed by its repo-relative path; "
                 "every comment block is tagged `[<path>:<line>]`.\n")
    if missing:
        parts.append("\nTracked-but-not-on-disk (pending deletion, NOT included):\n")
        for f in missing:
            parts.append(f"  - {f.relative_to(root)}\n")

    # --- 1) Markdown ---
    parts.append("\n" + "=" * 78 + "\n# PART 1 — MARKDOWN FILES\n" + "=" * 78 + "\n")
    for f in md_files:
        rel = f.relative_to(root)
        try:
            text = f.read_text(encoding="utf-8", errors="replace").rstrip()
        except OSError as e:
            text = f"<<could not read: {e}>>"
        parts.append(f"\n{'-' * 78}\n# SOURCE: {rel}\n{'-' * 78}\n\n{text}\n")

    # --- 2) code comments ---
    parts.append("\n" + "=" * 78 + "\n# PART 2 — CODE COMMENTS (by file)\n" + "=" * 78 + "\n")
    n_with_comments = 0
    for f in code_files:
        rel = f.relative_to(root)
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        comments = EXTRACTORS[COMMENT_LANG[f.suffix.lower()]](src)
        if not comments:
            continue
        n_with_comments += 1
        parts.append(f"\n{'-' * 78}\n# SOURCE: {rel}  ({len(comments)} comment blocks)\n"
                     f"{'-' * 78}\n")
        for ln, c in comments:
            # each block tagged with its exact origin so any line is traceable
            parts.append(f"\n[{rel}:{ln}]\n{c.rstrip()}\n")

    args.output.write_text("".join(parts), encoding="utf-8")
    total = args.output.stat().st_size
    print(f"wrote {args.output} — {len(md_files)} md files + comments from "
          f"{n_with_comments} source files ({total:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

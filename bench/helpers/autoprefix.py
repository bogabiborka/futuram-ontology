"""Helper: auto-fill/correct PREFIX declarations from the endpoint's VoID.

A pre-execution rewrite hook: an undeclared or wrong-namespace `prefix:local`
token is fixed from the VoID's prefix map (the only source of truth, never live data).

Public API:
    apply(sparql_query, endpoint_url) -> (rewritten_query, changes)
    namespaces(endpoint_url) -> {prefix: namespace}   # shared by helper_classcheck
"""
from __future__ import annotations

import json
import os
import pathlib
import re

from rdflib import Graph

# `prefix:local` usage and `PREFIX p:` declarations (keyword is case-insensitive).
_USE_RE = re.compile(r"(?<![\w:<])([A-Za-z][\w.\-]*):[A-Za-z0-9_\-%]")
_DECL_RE = re.compile(r"(?im)^\s*PREFIX\s+([A-Za-z][\w.\-]*)\s*:")
# Full declaration with its namespace, for correcting a wrong namespace in place.
_DECL_FULL_RE = re.compile(
    r"(?im)^[ \t]*PREFIX[ \t]+([A-Za-z][\w.\-]*)[ \t]*:[ \t]*<([^>]*)>[ \t]*$")

_NS_CACHE: dict[str, dict[str, str]] = {}      # endpoint_url -> {prefix: ns}
_VOIDPATH_CACHE: dict[str, str] | None = None  # endpoint_url -> void_file path

_SETTINGS = pathlib.Path(
    os.getenv("BENCH_SETTINGS",
              pathlib.Path(__file__).resolve().parent.parent / "settings.bench.json"))


def _void_path_for(endpoint_url: str) -> pathlib.Path | None:
    """Resolve the VoID Turtle for an endpoint from settings.bench.json. The
    settings file stores container paths (/app/...); map them back to the repo if
    that path is absent locally."""
    global _VOIDPATH_CACHE
    if _VOIDPATH_CACHE is None:
        _VOIDPATH_CACHE = {}
        try:
            cfg = json.loads(_SETTINGS.read_text())
            for ep in cfg.get("endpoints", []):
                if ep.get("endpoint_url") and ep.get("void_file"):
                    _VOIDPATH_CACHE[ep["endpoint_url"]] = ep["void_file"]
        except Exception:
            pass
    raw = _VOIDPATH_CACHE.get(endpoint_url)
    if not raw:
        return None
    p = pathlib.Path(raw)
    if p.exists():
        return p
    # container path -> local: try repo-relative by basename under sparql-llm/ & bench/
    repo = pathlib.Path(__file__).resolve().parent.parent.parent
    for cand in (repo / "sparql-llm" / p.name, repo / "bench" / p.name,
                 pathlib.Path(str(raw).replace("/app/", str(repo) + "/"))):
        if cand.exists():
            return cand
    return None


def namespaces(endpoint_url: str) -> dict[str, str]:
    """prefix -> namespace as declared in the endpoint's VoID. Cached."""
    if endpoint_url in _NS_CACHE:
        return _NS_CACHE[endpoint_url]
    ns: dict[str, str] = {}
    vp = _void_path_for(endpoint_url)
    if vp:
        try:
            g = Graph().parse(str(vp), format="turtle")
            for pfx, uri in g.namespaces():
                if pfx:
                    ns[str(pfx)] = str(uri)
        except Exception:
            pass
    _NS_CACHE[endpoint_url] = ns
    return ns


def apply(sparql_query: str, endpoint_url: str) -> tuple[str, dict]:
    """Fix PREFIXes (and near-miss full <…IRI> namespaces) against the VoID.
    Returns (query, changes) with changes={"corrected": [PREFIX labels rewritten to
    canonical ns], "added": [labels declared because used-but-undeclared],
    "iri_corrected": [full-IRI namespaces rewritten to the canonical VoID ns]};
    all empty -> query unchanged."""
    if not sparql_query or not sparql_query.strip():
        return sparql_query, {"corrected": [], "added": []}
    known = namespaces(endpoint_url)
    if not known:
        return sparql_query, {"corrected": [], "added": []}
    query = sparql_query
    fixed: list[str] = []

    # 1) CORRECT a declared-but-wrong namespace in place: if a declared prefix
    #    label is one the VoID knows but its namespace differs (classically
    #    futuram: with http:// vs the canonical https://), rewrite to the VoID's.
    def _rewrite(m: "re.Match") -> str:
        label, decl_ns = m.group(1), m.group(2)
        canon = known.get(label)
        if canon and decl_ns != canon:
            fixed.append(label)
            return f"PREFIX {label}: <{canon}>"
        return m.group(0)
    query = _DECL_FULL_RE.sub(_rewrite, query)

    # 1b) CORRECT a full <…IRI> whose namespace is a near-miss of a VoID namespace
    #     (classically the http:// vs https:// scheme typo, or a trailing-slash
    #     variant). The model sometimes spells a class as a full angle-bracket IRI
    #     rather than a prefixed name, so step 1's PREFIX-only fix never sees it and
    #     the one-character namespace typo silently returns zero rows. Only rewrite
    #     when the local part is preserved and exactly ONE VoID namespace matches the
    #     typo'd one under scheme/trailing-slash normalisation — never guess.
    def _norm(ns: str) -> str:
        return re.sub(r"^https?://", "", ns).rstrip("/#")

    canon_by_norm: dict[str, str] = {}
    for ns in known.values():
        canon_by_norm.setdefault(_norm(ns), ns)

    iri_fixed: list[str] = []

    def _fix_iri(m: "re.Match") -> str:
        full = m.group(1)
        # split into namespace + local at the last '#' or '/'
        cut = max(full.rfind("#"), full.rfind("/"))
        if cut < 0:
            return m.group(0)
        ns, local = full[:cut + 1], full[cut + 1:]
        canon = canon_by_norm.get(_norm(ns))
        if canon and ns != canon:
            iri_fixed.append(ns)
            return f"<{canon}{local}>"
        return m.group(0)

    query = re.sub(r"<([^>]+)>", _fix_iri, query)

    # 2) FILL a prefix the query uses but never declared.
    declared = {m.group(1) for m in _DECL_RE.finditer(query)}
    used = {m.group(1) for m in _USE_RE.finditer(query)}
    missing = sorted(
        p for p in (used - declared)
        if p in known and p not in {"http", "https", "urn", "mailto"})
    if missing:
        query = "".join(f"PREFIX {p}: <{known[p]}>\n" for p in missing) + query

    return query, {"corrected": fixed, "added": missing, "iri_corrected": iri_fixed}

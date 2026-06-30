"""Helper: block a query using a predicate the endpoint's VoID does not describe
(the predicate mirror of classcheck). Absent -> return a hint, naming the OTHER
endpoint's VoID if it belongs there. VoID-only source of truth.

Public API:
    check(sparql_query, endpoint_url) -> str | None
        None -> fine, execute.   str -> blocking hint, do NOT run.
"""
from __future__ import annotations

import json
import re
from difflib import get_close_matches

from rdflib import Graph
from rdflib import URIRef

from .autoprefix import namespaces, _void_path_for, _SETTINGS

_VOID_PROPERTY = URIRef("http://rdfs.org/ns/void#property")

# endpoint -> {"iris": set, "by_local": {local_lc: iri}}
_CACHE: dict[str, dict] = {}
# {other_endpoint_url: {pred_iri, ...}} for the cross-endpoint note
_OTHERS_CACHE: dict[str, dict[str, set]] | None = None

# Standard-vocab predicates we never flag (always legitimate).
_SKIP_NS = ("http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            "http://www.w3.org/2000/01/rdf-schema#",
            "http://www.w3.org/2002/07/owl#",
            "http://www.w3.org/2001/XMLSchema#",
            "http://www.w3.org/2004/02/skos/core#",
            "http://rdfs.org/ns/void#", "http://ldf.fi/void-ext#")

# property-path separators so we split a/b, a|b, a/b* into individual predicates
_PATH_SPLIT = re.compile(r"[/|^]|\*|\+|\?")
# PREFIX declarations IN THE QUERY (the model may declare a prefix the VoID does
# not — e.g. the other backend's fq: — and we must expand it to catch the leak).
_QDECL_RE = re.compile(r"(?im)^\s*PREFIX\s+([A-Za-z][\w.\-]*)\s*:\s*<([^>]+)>")
# VALUES blocks: VALUES ?v { ... } or VALUES (?a ?b) { ... }. The brace-list is a
# run of OBJECT-position terms; scanning it for predicates misreads each item as
# the verb of the previous one. Strip the whole block before predicate extraction.
_VALUES_BLOCK = re.compile(r"(?is)\bVALUES\b\s*(?:\([^)]*\)|\?\w+)\s*\{[^}]*\}")


def _query_prefixes(query: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _QDECL_RE.finditer(query)}


def _inventory(endpoint_url: str) -> dict:
    if endpoint_url in _CACHE:
        return _CACHE[endpoint_url]
    iris: set[str] = set()
    by_local: dict[str, str] = {}
    vp = _void_path_for(endpoint_url)
    if vp:
        try:
            g = Graph().parse(str(vp), format="turtle")
            for p in g.objects(None, _VOID_PROPERTY):
                iri = str(p)
                iris.add(iri)
                local = re.split(r"[#/]", iri)[-1]
                if local:
                    by_local.setdefault(local.lower(), iri)
        except Exception:
            pass
    _CACHE[endpoint_url] = {"iris": iris, "by_local": by_local}
    return _CACHE[endpoint_url]


def _other_endpoints(this_url: str) -> dict[str, set]:
    """{other_endpoint_url: predicate_iris} for every OTHER endpoint in settings,
    so we can tell the model when its predicate belongs to the other backend."""
    global _OTHERS_CACHE
    if _OTHERS_CACHE is None:
        _OTHERS_CACHE = {}
        try:
            cfg = json.loads(_SETTINGS.read_text())
            for ep in cfg.get("endpoints", []):
                url = ep.get("endpoint_url")
                if url:
                    _OTHERS_CACHE[url] = _inventory(url)["iris"]
        except Exception:
            pass
    return {u: preds for u, preds in (_OTHERS_CACHE or {}).items() if u != this_url}


# one atom: a full <IRI>, a prefixed name, the `a` shorthand, or a var.
_ATOM = r"(?:<[^>]+>|[A-Za-z][\w.\-]*:[A-Za-z0-9_\-%.]+|\ba\b|\?[A-Za-z_]\w*)"
# a single SPARQL term for the S-P-O walk. A PROPERTY PATH (atom + path-op + atom,
# e.g. `crit:remark/crit:importance`, `rdfs:subClassOf*`) is consumed as ONE term so
# the operator never splits the walk and pushes the path tail into object position.
_TERM = re.compile(
    _ATOM + r"(?:\s*[/|^]\s*" + _ATOM + r"|\s*[*+?])*"   # atom with optional path
    r"|[.;,\[\]]"                                          # triple punctuation
)


def _predicate_tokens(query: str) -> set[str]:
    """Predicate verbs in the query, found by WALKING the triple structure rather
    than regex-after-a-separator (which mis-grabs a property-path OBJECT — e.g.
    `crit:CRITICAL` in `crit:remark/crit:importance crit:CRITICAL` — as a verb and
    falsely rejects a valid query). A triple is subject · predicate · object; the
    slot advances S→P→O, resets to P on `;`, to O on `,`, to S on `.`, and a `[`
    opens a fresh blank-node subject. Only the token in the PREDICATE slot counts;
    property paths are split into individual predicates."""
    toks: set[str] = set()
    body = query
    # strip the PREFIX header so declarations aren't mistaken for triples
    body = _QDECL_RE.sub("", body)
    # strip VALUES blocks — their brace-list is a run of OBJECT-position terms.
    body = _VALUES_BLOCK.sub(" ", body)
    # only consider the WHERE body — SELECT-clause exprs (SUM(...), BIND, etc.)
    # contain no triples but do contain prefixed names that must not read as verbs.
    mwhere = re.search(r"(?is)\bWHERE\b\s*\{", body)
    if mwhere:
        body = body[mwhere.end():]

    slot = "S"               # next term fills this slot
    bnode_depth = 0          # inside [ ... ] predicate-object lists
    for m in _TERM.finditer(body):
        t = m.group(0)
        if t == ".":
            slot = "S"; continue
        if t == ";":
            slot = "P"; continue
        if t == ",":
            slot = "O"; continue
        if t == "[":
            # `[` is an OBJECT that is itself a fresh subject; next term is its pred
            bnode_depth += 1; slot = "P"; continue
        if t == "]":
            bnode_depth = max(0, bnode_depth - 1); slot = "O"; continue
        # a real term (var / IRI / prefixed name / `a`)
        if slot == "P":
            if t != "a" and not t.startswith("?"):
                for part in _PATH_SPLIT.split(t):
                    part = part.strip("() ")
                    if part and part != "a" and not part.startswith("?"):
                        toks.add(part)
            slot = "O"
        elif slot == "S":
            slot = "P"
        else:  # slot == "O": object filled; next term must follow a separator
            slot = "O"
    return toks


# Predicate existence in the live data, cached per (endpoint, iri).
_EXISTS_CACHE: dict[tuple, bool] = {}


def _exists_in_data(endpoint_url: str, iri: str) -> bool:
    key = (endpoint_url, iri)
    if key in _EXISTS_CACHE:
        return _EXISTS_CACHE[key]
    try:
        from rdflib.plugins.stores.sparqlstore import SPARQLStore
        g = Graph(SPARQLStore(endpoint_url))
        ans = bool(g.query(f"ASK {{ ?s <{iri}> ?o }}").askAnswer)
    except Exception:
        ans = True       # never block on a probe failure (fail open)
    _EXISTS_CACHE[key] = ans
    return ans


def _expand(tok: str, ns: dict[str, str]) -> str | None:
    if tok.startswith("<") and tok.endswith(">"):
        return tok[1:-1]
    if ":" in tok:
        pfx, local = tok.split(":", 1)
        base = ns.get(pfx)
        if base:
            return base + local
    return None


def check(sparql_query: str, endpoint_url: str) -> str | None:
    if not sparql_query:
        return None
    inv = _inventory(endpoint_url)
    if not inv["iris"]:                       # no VoID property inventory -> skip
        return None
    # VoID prefixes + prefixes the query itself declares (so a cross-endpoint
    # prefix the model brought in, like fq:, still expands and gets caught).
    ns = {**namespaces(endpoint_url), **_query_prefixes(sparql_query)}
    others = _other_endpoints(endpoint_url)

    for tok in _predicate_tokens(sparql_query):
        iri = _expand(tok, ns)
        if not iri or any(iri.startswith(s) for s in _SKIP_NS):
            continue
        if iri in inv["iris"]:
            continue
        # Only guard predicates in a namespace the VoID actually covers, so we
        # don't flag a legitimately-uncatalogued vocabulary.
        local = re.split(r"[#/]", iri)[-1]
        ns_base = iri[: len(iri) - len(local)]
        covered_here = any(p.startswith(ns_base) for p in inv["iris"])

        # Cross-endpoint: does this predicate belong to ANOTHER backend?
        belongs_to = [u for u, preds in others.items() if iri in preds]
        if not covered_here and not belongs_to:
            continue  # unknown namespace we don't catalogue — leave it alone

        # SAFETY: never block a predicate that ACTUALLY EXISTS in this endpoint's
        # data even if the VoID omits it — block means provably absent from data (a
        # cross-endpoint fq: leak is still absent here, so correctly blocked).
        if _exists_in_data(endpoint_url, iri):
            continue

        # WRONG-NAMESPACE detection: the SAME local name may exist HERE under a
        # different namespace (classic FUT futuram# vs FQ futuram/query# mix-up).
        # That's a swap-namespace-here fix, NOT a wrong-backend error — say so.
        here_iri = inv["by_local"].get(local.lower())
        wrong_ns = here_iri is not None and here_iri != iri
        # Render the correct token as <prefix>:<local> when a VoID prefix matches.
        def _curie(target_iri: str) -> str:
            for pfx, base in ns.items():
                if pfx and base and target_iri.startswith(base):
                    return f"{pfx}:{target_iri[len(base):]}"
            return f"<{target_iri}>"

        msg = [
            f"BLOCKED — the predicate <{iri}> is not in this endpoint's VoID, so "
            f"the query was NOT run (it would return nothing and waste a step)."]
        if wrong_ns:
            # the local name IS served here, just under another namespace
            msg.append(
                f"WRONG NAMESPACE — this endpoint serves it as {_curie(here_iri)} "
                f"(<{here_iri}>), NOT <{iri}>. Use {_curie(here_iri)} on THIS "
                f"endpoint; do not change endpoints.")
        elif belongs_to:
            msg.append("That predicate belongs to a DIFFERENT backend (" +
                       ", ".join(belongs_to) + "), not this one, and its local name "
                       "is not served here under any namespace. Do NOT mix "
                       "vocabularies across endpoints — use only the predicates "
                       "this endpoint's VoID documents.")
        else:
            msg.append("Use only predicates this endpoint's VoID documents; read "
                       "them with search_sparql_docs / get_classes_schema.")
        # Suggestions: skip when we already gave the exact wrong-namespace fix.
        if not wrong_ns:
            cands = get_close_matches(local.lower(), list(inv["by_local"]),
                                      n=6, cutoff=0.4)
            hint = [_curie(inv["by_local"][c]) for c in cands]
            if hint:
                msg.append("Predicates available here that resemble \"" + local +
                           "\": " + ", ".join(hint) + ".")
        return "\n".join(msg)
    return None

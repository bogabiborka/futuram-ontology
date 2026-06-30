"""Helper: when a valid query returns ZERO rows, find which triple pattern emptied
it — probe each WHERE pattern ALONE (ASK), grow the conjunction to find the failing
join, then check a FILTER. Queries the live endpoint only to DIAGNOSE (never block).

Public API:
    diagnose(sparql_query, endpoint_url) -> str | None
        None -> could not localise (or query not diagnosable); say nothing.
        str  -> a hint naming the empty/failing pattern, to append to the result.
"""
from __future__ import annotations

import re

from rdflib import Graph
from rdflib.plugins.stores.sparqlstore import SPARQLStore

# Pull the PREFIX header (so probes resolve the same prefixes) and the WHERE block.
_PREFIX_RE = re.compile(r"(?im)^\s*PREFIX\s+[^\n]+$")
_WHERE_RE = re.compile(r"(?is)\bWHERE\b\s*\{(.*)\}\s*"
                       r"(?:GROUP|ORDER|LIMIT|OFFSET|HAVING|VALUES|$)")

# A simple triple pattern terminated by . or ; — good enough for the BGPs the
# model writes. Sub-braces (OPTIONAL/UNION/subselect/FILTER) are skipped in the
# per-pattern probe, but a non-empty BGP-minus-filters flags a FILTER culprit.
_TRIPLE_RE = re.compile(r"([^.;{}]+?)\s*[.;]")
_FILTER_RE = re.compile(r"(?is)\bFILTER\b\s*\(")


def _header(query: str) -> str:
    return "\n".join(m.group(0).strip() for m in _PREFIX_RE.finditer(query))


def _union_branches(body: str) -> list[str]:
    """Split a WHERE body into its top-level UNION branches, returning the INSIDE
    of each `{ ... }` group. Only handles a flat `{ A } UNION { B } UNION { C }`
    shape (the common model form); returns [] if it doesn't look like that."""
    groups: list[str] = []
    depth = 0
    start = None
    for i, ch in enumerate(body):
        if ch == "{":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                groups.append(body[start:i])
                start = None
    # require the groups to be joined by UNION (not just sequential graph groups)
    if len(groups) >= 2 and re.search(r"\}\s*UNION\s*\{", body, re.IGNORECASE):
        return groups
    return []


def _where_body(query: str) -> str | None:
    m = _WHERE_RE.search(query)
    if not m:
        # no GROUP/ORDER/LIMIT tail: take the last {...}
        i = query.find("{")
        j = query.rfind("}")
        if i == -1 or j <= i:
            return None
        return query[i + 1:j]
    return m.group(1)


def _ask(g: Graph, header: str, body: str) -> bool | None:
    try:
        return bool(g.query(f"{header}\nASK {{ {body} }}").askAnswer)
    except Exception:
        return None


def diagnose(sparql_query: str, endpoint_url: str) -> str | None:
    body = _where_body(sparql_query)
    if not body:
        return None
    header = _header(sparql_query)
    g = Graph(SPARQLStore(endpoint_url))

    inner = body
    # NESTED GROUPS (OPTIONAL/UNION/subselect): per-triple decomposition is
    # unreliable, but still owe FEEDBACK. For a top-level UNION, probe each branch
    # alone; if all empty that's the story, else name the constructs to re-examine.
    if "{" in inner or "}" in inner:
        branches = _union_branches(inner)
        if len(branches) >= 2:
            empties = []
            for b in branches:
                # only probe branches that are themselves flat (no further nesting)
                if "{" in b or "}" in b:
                    empties = []
                    break
                if _ask(g, header, b) is False:
                    empties.append(b.strip())
            if empties and len(empties) == len(branches):
                return ("[diagnose] The query returned nothing because EVERY branch "
                        "of your UNION is empty on its own. Each alternative matches "
                        "no data — re-check the class/predicate in each. First empty "
                        "branch: `" + empties[0] + "`.")
            if empties:
                return ("[diagnose] These UNION branch(es) match NO data and "
                        "contribute nothing: `" + "`, `".join(empties)
                        + "`. Fix or drop them; the non-empty branch(es) are why the "
                        "rest of the query shape may still be wrong.")
        return ("[diagnose] The query is valid but returned nothing, and it uses "
                "nested groups (UNION/OPTIONAL/subquery) I can't decompose. Simplify "
                "to find the cause: run the SMALLEST core pattern alone first (e.g. "
                "just the whole and its direct link), confirm it returns rows, then "
                "add one clause at a time. A common cause here is an OPTIONAL/UNION "
                "branch whose join variable never binds.")

    # Split into FILTERs and plain triples.
    has_filter = bool(_FILTER_RE.search(inner))
    no_filter = re.sub(r"(?is)\bFILTER\b\s*\([^)]*\)", "", inner).strip(" .;\n")
    triples = [t.strip() for t in _TRIPLE_RE.findall(no_filter + ".") if t.strip()]
    if not triples:
        return None

    # 1) Each triple ALONE: a triple with no matches at all is a direct culprit.
    for t in triples:
        ans = _ask(g, header, t + " .")
        if ans is False:
            return ("[diagnose] The query returned nothing because this pattern "
                    "matches NO data on its own: `" + t.strip() + "`. Check its "
                    "class/predicate/term — that triple is what empties the result.")

    # 2) Conjunction grows one triple at a time: the triple that drops a non-empty
    #    running match to empty is the failing JOIN.
    running = []
    prev_ok = True
    for t in triples:
        running.append(t + " .")
        ans = _ask(g, header, " ".join(running))
        if ans is False and prev_ok:
            return ("[diagnose] Each pattern matches on its own, but ADDING `"
                    + t.strip() + "` makes the result empty — that JOIN fails "
                    "(the variables it shares don't line up with the rest). "
                    "Rethink how that pattern connects.")
        prev_ok = ans is not False

    # 3) All triples together match, so a FILTER is too strict.
    if has_filter:
        ans = _ask(g, header, no_filter)
        if ans is True:
            return ("[diagnose] The graph patterns DO match data; your FILTER is "
                    "what removes everything. Loosen or correct the FILTER "
                    "(e.g. case with LCASE(), a wrong literal/datatype, or "
                    "an over-tight numeric/string condition).")
    return None

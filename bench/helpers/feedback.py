"""Helper: guarantee every execute_sparql_query result hands the LLM back REAL
DATA or ACTIONABLE FEEDBACK. Classifies a non-data outcome (validation/error/
empty/no-content) and appends a next-step steer. LAST step; no-op on real rows.

Public API:
    augment(content, *, tool, ok, grounded, had_diagnosis) -> str
        Returns content unchanged when it already carries data/feedback, else
        content plus a "[next]" steer line.
"""
from __future__ import annotations

_VALIDATION = "not valid according to"
_NO_RESULTS = "returned no results"
_ERROR = "returned error"
_NO_CONTENT = "(no text content)"
_FAILED = "failed:"        # "tool <fn> failed: <e>" from the harness


def _classify(content: str) -> str:
    c = content.lower()
    if _VALIDATION in c:
        return "validation"
    if _NO_RESULTS in c:
        return "empty"
    if _ERROR in c or "syntax error" in c or "queryexception" in c:
        return "sparql_error"
    if _NO_CONTENT in content or content.strip() == "":
        return "no_content"
    if _FAILED in c:
        return "tool_error"
    return "data"


_STEER = {
    "validation": (
        "[next] The query was REJECTED by the endpoint's VoID validator before "
        "running — it is not malformed SPARQL, it references a class/property/shape "
        "the VoID does not allow in that position. Re-read the VoID via "
        "search_sparql_docs / get_classes_schema and use only the predicates and "
        "classes it documents, in the positions it documents them. Do not re-submit "
        "the same query."),
    "sparql_error": (
        "[next] The endpoint could not execute this query (a SPARQL syntax or "
        "runtime error — see the message above). Fix the exact spot it names: check "
        "balanced braces, a missing '.' between triples, an undeclared prefix, or a "
        "bad aggregate/BIND. Change the query before retrying; do not resubmit it "
        "unchanged."),
    "empty": (
        "[next] The query is valid but matched no data. This does NOT mean the "
        "thing is missing — do not conclude the class/whole is absent and do not "
        "give up. Relax step by step: run the core pattern (the whole and its "
        "direct link) alone to confirm it returns rows, then add one clause at a "
        "time until the result disappears — that last clause is the problem. If you "
        "EARLIER query already returned rows for this whole, keep using that IRI."),
    "no_content": (
        "[next] The tool returned nothing usable. Re-issue a concrete, well-formed "
        "execute_sparql_query (with the endpoint pinned) — or, if you were "
        "exploring, query the schema first: SELECT DISTINCT ?c WHERE { ?s a ?c } "
        "to see the available classes."),
    "tool_error": (
        "[next] The tool call itself errored (see above). Re-check the arguments — "
        "the SPARQL string must be non-empty and the endpoint_url exactly the one "
        "you were told to use — then try again."),
}


import re as _re

# Skill pointer appended to empty/error results (the #1 silent cause is a wrong
# namespace): points at the `prefixes` skill (exact VoID-derived namespaces) and
# list_skills for the method skill.
_SKILL_POINTER = (
    " You have SKILLS for this — call get_skill(\"prefixes\") to get the EXACT "
    "namespaces for this endpoint (a wrong scheme/host/fragment silently returns "
    "nothing), and list_skills(endpoint_url) for the method skill that fits this "
    "question. Read the relevant skill before your next query.")


def augment(content: str, *, tool: str, ok: bool, grounded: bool,
            had_diagnosis: bool, sparql_query: str | None = None) -> str:
    """Ensure the result is data or carries actionable feedback. No-op for data
    and for non-SPARQL tools."""
    if tool != "execute_sparql_query":
        return content
    if grounded:                      # real rows == the feedback
        return content
    kind = _classify(content)
    if kind == "data":               # classifier saw usable content
        return content
    # Don't duplicate a steer if one is somehow already present. But STILL add the
    # skill pointer to an empty/error result if it isn't there yet (the diagnoser's
    # per-query hint doesn't mention skills).
    base = content
    if "[next]" not in base:
        steer = _STEER.get(kind)
        # The diagnoser already gave a tailored empty steer; skip the generic one.
        if steer and not (kind == "empty" and had_diagnosis):
            base = base + "\n" + steer
    # On an empty/error result where the query DECLARED a namespace, remind the
    # model it has skills — especially the prefixes skill — since a wrong namespace
    # is the most common silent cause. Added once.
    declares_prefix = bool(sparql_query and _re.search(
        r"(?im)^\s*PREFIX\s", sparql_query))
    if kind in ("empty", "sparql_error", "validation") and declares_prefix \
            and "get_skill(\"prefixes\")" not in base:
        base = base + _SKILL_POINTER
    return base

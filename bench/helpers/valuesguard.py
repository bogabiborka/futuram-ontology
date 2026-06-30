"""Pre-exec guard: FORBID hand-listing a SET of concrete class IRIs.

The single biggest fq/composition error is enumerating the members of a group by
hand — a `VALUES { :X1 :X2 :X3 … }` block, an `IN (:X1, :X2, …)`, or a chain of
`?c = :X1 || ?c = :X2 || …` — instead of DISCOVERING the set from the data's own
`rdfs:subClassOf` edges. A hand-typed set is reliably WRONG (it omits members the
text never named: an extra family, an averaged "unspecified" class). The skills say
"never hand-list; walk the subClassOf hierarchy", but the model ignores it — so this
guard makes it a HARD block: a query that hand-lists 3+ concrete domain class IRIs is
NOT run, and the model is told to re-write it as a `rdfs:subClassOf` discovery query.

What is NOT blocked:
- a single concrete IRI as the SUBJECT of a query (asking about one resolved class is
  fine — that is the normal case);
- a VALUES/IN with only 1-2 concrete IRIs (a small, legitimately-fixed pair);
- VALUES over LITERALS, numbers, or non-domain IRIs (years, units, std vocab).
Only an enumerated SET (>=3) of `futuram:`-namespaced CLASS IRIs trips it — that is
always a group that should be discovered, never typed.
"""
from __future__ import annotations

import re

# the domain namespaces whose class IRIs must be discovered, not enumerated
_DOMAIN_NS = (
    "https://www.purl.org/futuram#",
    "https://www.purl.org/futuram/query#",
)
_MIN_HANDLISTED = 3   # 3+ enumerated domain class IRIs = a hand-listed set

# a VALUES block: VALUES ?v { … }  or  VALUES (?a ?b) { … }
_VALUES = re.compile(r"\bVALUES\b\s*\(?[^{}]*?\)?\s*\{([^{}]*)\}", re.IGNORECASE | re.DOTALL)
# an IN (...) / NOT IN (...) list
_IN = re.compile(r"\b(?:NOT\s+)?IN\s*\(([^()]*)\)", re.IGNORECASE | re.DOTALL)
# a domain class IRI written full <…#Local> or prefixed futuram:Local / fq:Local
_FULL_IRI = re.compile(r"<(https://www\.purl\.org/futuram(?:/query)?#[^>\s]+)>")
_PREFIXED = re.compile(r"\b(?:futuram|fq)\s*:\s*[A-Za-z_][A-Za-z0-9_]*", re.IGNORECASE)


def _count_domain_iris(blob: str) -> int:
    """How many distinct concrete domain CLASS IRIs are enumerated in `blob`."""
    iris = set()
    for m in _FULL_IRI.finditer(blob):
        iris.add(m.group(1))
    for m in _PREFIXED.finditer(blob):
        iris.add(re.sub(r"\s+", "", m.group(0)).lower())
    return len(iris)


def _equality_chain_iris(q: str) -> int:
    """The MAX run of `?v = <domainIRI>` alternatives OR'd together (a hand-listed
    set written as a FILTER equality chain)."""
    # find ?var = <iri|prefixed> tokens, then the longest || run on one variable
    eqs = re.findall(
        r"\?\w+\s*=\s*(?:<https://www\.purl\.org/futuram(?:/query)?#[^>\s]+>"
        r"|(?:futuram|fq)\s*:\s*[A-Za-z_][A-Za-z0-9_]*)",
        q, re.IGNORECASE)
    # crude but safe: if 3+ such equalities appear in the query, treat as a chain
    return len(eqs)


def check(sparql_query: str, endpoint_url: str | None = None) -> str | None:
    """Return a blocking hint if the query hand-lists a SET (>=3) of concrete domain
    class IRIs (VALUES / IN / equality-chain), else None."""
    q = sparql_query or ""
    worst = 0
    for m in _VALUES.finditer(q):
        worst = max(worst, _count_domain_iris(m.group(1)))
    for m in _IN.finditer(q):
        worst = max(worst, _count_domain_iris(m.group(1)))
    worst = max(worst, _equality_chain_iris(q))
    if worst < _MIN_HANDLISTED:
        return None
    return "\n".join([
        f"BLOCKED — this query HAND-LISTS {worst} class IRIs (in a VALUES / IN / "
        "equality chain). That query was NOT run.",
        "Enumerating the members of a group by hand is the #1 wrong answer here: the "
        "data almost always has MORE members than you typed (a family or an averaged "
        "'unspecified' class the question never named), so a hand-typed set is short "
        "and scores WRONG.",
        "Discover the set from the data instead. The members of a group are its "
        "rdfs:subClassOf children — resolve the ONE parent/family class, then:",
        "  - one number PER member  -> `?sub rdfs:subClassOf <ParentClassIRI>` "
        "(direct), one row each;",
        "  - a TOTAL/LIST over a kind -> `?key rdfs:subClassOf* <FamilyClassIRI>` "
        "(transitive closure), inside the fixed whole.",
        "Get the `per-subclass-breakdown-fq` or `total-over-kind-fq` skill for the "
        "exact pattern. Re-write with rdfs:subClassOf and run that — never a typed list.",
    ])

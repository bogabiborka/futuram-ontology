"""Pre-exec guard: FORBID string-matching on a CLASS IRI.

A class IRI is an opaque/random identifier — its human meaning lives only in
`rdfs:label` and `rdfs:comment`. Filtering classes by matching the IRI string
(`CONTAINS(STR(?c), …)`, `REGEX(STR(?c), …)`, `STRSTARTS(STR(?c), …)`, …) is
wrong: it assumes the IRI spells the concept (it does not) and silently
misses / mis-matches. This guard blocks such a query before it runs.

A variable is treated as a CLASS purely by its ROLE in the graph pattern — NOT by
its name. `?v` is a class iff some triple binds it that way:
  * `?v rdf:type owl:Class`  (or `a owl:Class`)            — explicitly a class
  * `?v rdfs:subClassOf …`  or  `… rdfs:subClassOf ?v`     — a class by subClassOf domain/range
  * `… rdf:type ?v`  (or `… a ?v`)                          — the OBJECT of rdf:type is a class (rdfs:range owl:Class)
So a `STR()` op on a label/comment/literal/instance variable is NOT blocked —
only one on a variable the pattern actually uses as a class.
"""
from __future__ import annotations

import re

# CONTAINS/REGEX/STRSTARTS/…( … STR(?var) … ) — the offending string op on ?var.
_STR_OP = re.compile(
    r"\b(CONTAINS|REGEX|STRSTARTS|STRENDS|STRBEFORE|STRAFTER)\s*\(\s*"
    r"(?:LCASE|UCASE)?\s*\(?\s*STR\s*\(\s*\?([A-Za-z_][A-Za-z0-9_]*)\s*\)",
    re.IGNORECASE,
)


def _is_namespace_token(needle: str) -> bool:
    """True if the matched literal looks like a NAMESPACE / scheme / URI fragment
    (so matching it on a class IRI is a vocabulary test, not concept-fishing):
      * contains a URI structural char  / # :   or a dot (host/path),
      * is an all-caps / caps+digits ontology scheme token (CHEBI, OBO, EC, GO,
        CHEBI_33319), i.e. no lowercase letters,
      * is empty / not extracted (be lenient — when unsure, don't block).
    A concept WORD (an ordinary lowercase term naming a material/element/category)
    has lowercase letters and no URI structure, so it is NOT a namespace token and
    stays blocked."""
    if not needle:
        return True                       # couldn't read the literal -> fail open
    if any(ch in needle for ch in "/#:.") or "obo" in needle.lower():
        return True
    core = needle.replace("_", "").replace("-", "")
    # a scheme token has letters but NO lowercase letter (CHEBI, GO, EC, CHEBI_33319)
    if core and not any(c.islower() for c in core) and any(c.isalpha() for c in core):
        return True
    return False


def _class_vars(query: str) -> set[str]:
    """The set of variables the query binds AS A CLASS, by graph role (domain/range
    of rdf:type / owl:Class / rdfs:subClassOf) — never by variable name."""
    out: set[str] = set()
    V = r"\?([A-Za-z_][A-Za-z0-9_]*)"
    # accept the prefixed name OR the full <IRI> for each vocabulary term, so a
    # query that spells owl:Class / rdf:type / rdfs:subClassOf as a full IRI is
    # recognised too (the model often does).
    TYPE = r"(?:a|rdf:type|<http://www\.w3\.org/1999/02/22-rdf-syntax-ns#type>)"
    OWLCLASS = r"(?:owl:Class|<http://www\.w3\.org/2002/07/owl#Class>)"
    SUBCLASS = r"(?:rdfs:subClassOf|<http://www\.w3\.org/2000/01/rdf-schema#subClassOf>)\*?"
    # ?v a/rdf:type owl:Class   (explicit class typing)
    for m in re.finditer(rf"{V}\s+{TYPE}\s+{OWLCLASS}", query, re.I):
        out.add(m.group(1))
    # ?v rdfs:subClassOf …      (subject of subClassOf is a class)
    for m in re.finditer(rf"{V}\s+{SUBCLASS}", query, re.I):
        out.add(m.group(1))
    # … rdfs:subClassOf ?v      (object of subClassOf is a class)
    for m in re.finditer(rf"{SUBCLASS}\s+{V}", query, re.I):
        out.add(m.group(1))
    # … a/rdf:type ?v           (object of rdf:type is a class — rdfs:range owl:Class)
    for m in re.finditer(rf"{TYPE}\s+{V}", query, re.I):
        out.add(m.group(1))
    return out


def check(query: str, endpoint_url: str | None = None) -> str | None:
    """Return a corrective message if the query string-matches a CLASS IRI (a
    variable bound as a class by its graph role); else None. Signature mirrors the
    other pre-exec guards (classcheck / predicatecheck)."""
    if not query:
        return None
    class_vars = _class_vars(query)
    if not class_vars:
        return None
    for m in _STR_OP.finditer(query):
        op, var = m.group(1), m.group(2)
        if var not in class_vars:
            continue
        # ALLOW matching a NAMESPACE / scheme fragment — `CONTAINS(STR(?c),"CHEBI")`
        # asks "is this (super)class a ChEBI class", a legitimate vocabulary test,
        # NOT concept-fishing in an opaque id. We only forbid matching a CONCEPT word
        # (a human term like "copper"/"critical" the opaque IRI does NOT spell). The
        # matched literal is read from just after this STR-op; a literal that is a
        # known namespace token / URI fragment (a scheme word like CHEBI, an all-caps
        # or path/host fragment) is allowed, an ordinary lexical word is blocked.
        tail = query[m.end(): m.end() + 200]
        lit = re.search(r'["\']([^"\']+)["\']', tail)
        needle = lit.group(1) if lit else ""
        if _is_namespace_token(needle):
            continue
        return (
                "QUERY BLOCKED — you are string-matching on a class IRI "
                f"(`{op}(… STR(?{var}) …)`, and ?{var} is bound as a class). The "
                "class IRI is an OPAQUE, random identifier; its meaning is NOT in "
                "the URI. Filtering the IRI string is forbidden and will miss or "
                "mis-match the class.\n"
                "Search the class's MEANING instead — its rdfs:label / rdfs:comment:\n"
                f"  ?{var} a owl:Class .\n"
                f"  OPTIONAL {{ ?{var} rdfs:label ?label }}\n"
                f"  OPTIONAL {{ ?{var} rdfs:comment ?comment }}\n"
                "  FILTER( CONTAINS(LCASE(STR(?label)),   \"<term>\")\n"
                "       || CONTAINS(LCASE(STR(?comment)), \"<term>\") )\n"
                f"Rewrite to filter ?label / ?comment (never STR(?{var})), then run again.")
    return None

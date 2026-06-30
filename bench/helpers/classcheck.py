"""Helper: block a query targeting a class the VoID does not describe, with the
closest available classes as hints. Block decision is VoID-only; "did you mean"
hints are VoID-first with a live fallback (subclasses/instances under a known parent).

Public API:
    check(sparql_query, endpoint_url) -> str | None
        None -> fine, execute.   str -> blocking hint, do NOT run.
"""
from __future__ import annotations

import re
from difflib import get_close_matches

from rdflib import Graph
from rdflib.namespace import RDFS
from rdflib import URIRef
from rdflib.plugins.stores.sparqlstore import SPARQLStore

from .autoprefix import namespaces, _void_path_for

_VOID_CLASS = URIRef("http://rdfs.org/ns/void#class")

# endpoint -> {"iris": set, "by_local": {local_lc: iri}, "by_label": {label_lc: iri}}
_CACHE: dict[str, dict] = {}

_TYPE_POS = re.compile(
    r"(?:\ba\b|rdf:type|<http://www\.w3\.org/1999/02/22-rdf-syntax-ns#type>"
    r"|rdfs:subClassOf\*?|<http://www\.w3\.org/2000/01/rdf-schema#subClassOf>\*?)"
    r"\s+(<[^>]+>|[A-Za-z][\w.\-]*:[A-Za-z0-9_\-%.]+)")
_SUBJ_OF_SUBCLASS = re.compile(
    r"(<[^>]+>|[A-Za-z][\w.\-]*:[A-Za-z0-9_\-%.]+)\s+"
    r"(?:rdfs:subClassOf\*?|<http://www\.w3\.org/2000/01/rdf-schema#subClassOf>\*?)")

_SKIP_NS = ("http://www.w3.org/", "http://rdfs.org/ns/void#",
            "http://ldf.fi/void-ext#")


def _inventory(endpoint_url: str) -> dict:
    if endpoint_url in _CACHE:
        return _CACHE[endpoint_url]
    iris: set[str] = set()
    by_local: dict[str, str] = {}
    by_label: dict[str, str] = {}
    vp = _void_path_for(endpoint_url)
    if vp:
        try:
            g = Graph().parse(str(vp), format="turtle")
            for blk, cls in g.subject_objects(_VOID_CLASS):
                iri = str(cls)
                iris.add(iri)
                local = re.split(r"[#/]", iri)[-1]
                if local:
                    by_local.setdefault(local.lower(), iri)
                # the label sits on the partition block that carries void:class
                for lbl in g.objects(blk, RDFS.label):
                    by_label.setdefault(str(lbl).lower(), iri)
        except Exception:
            pass
    _CACHE[endpoint_url] = {"iris": iris, "by_local": by_local,
                            "by_label": by_label}
    return _CACHE[endpoint_url]


def _live_candidates(endpoint_url: str, inv: dict, term: str,
                     ns_base: str) -> list[str]:
    """LIVE FALLBACK hints (block decision already made VoID-only): enumerate the
    actual subclasses/instance-types under VoID-known parents sharing the missing
    IRI's namespace, ranked against the term. Returns local names, best first."""
    # parents = VoID-known classes in the same namespace as the missing IRI
    parents = sorted({c for c in inv["iris"] if c.startswith(ns_base)})
    if not parents:
        return []
    # don't enumerate under huge numbers of parents; the base kinds are few
    parents = parents[:12]
    g = Graph(SPARQLStore(endpoint_url))
    found: set[str] = set()
    for p in parents:
        for q in (
            # real subclasses of a VoID-known class
            f"SELECT DISTINCT ?c WHERE {{ ?c <http://www.w3.org/2000/01/rdf-schema#subClassOf>* <{p}> }} LIMIT 500",
            # instance-types that roll up to a VoID-known class
            f"SELECT DISTINCT ?c WHERE {{ ?i a ?c . ?c <http://www.w3.org/2000/01/rdf-schema#subClassOf>* <{p}> }} LIMIT 500",
        ):
            try:
                for r in g.query(q):
                    local = re.split(r"[#/]", str(r.c))[-1]
                    if local:
                        found.add(local)
            except Exception:
                pass
    if not found:
        return []
    ranked = get_close_matches(term.lower(),
                               [f.lower() for f in found], n=8, cutoff=0.3)
    lower_to_orig = {f.lower(): f for f in found}
    out = [lower_to_orig[r] for r in ranked]
    # if fuzzy gives nothing, fall back to substring matches, then a short sample
    if not out:
        out = [f for f in sorted(found) if term.lower() in f.lower()][:8]
    if not out:
        out = sorted(found)[:8]
    return out


# IRI existence in the live data (subject OR object), cached per (endpoint, iri).
_EXISTS_CACHE: dict[tuple, bool] = {}


def _exists_in_data(endpoint_url: str, iri: str) -> bool:
    key = (endpoint_url, iri)
    if key in _EXISTS_CACHE:
        return _EXISTS_CACHE[key]
    try:
        g = Graph(SPARQLStore(endpoint_url))
        ans = bool(g.query(
            f"ASK {{ {{ <{iri}> ?p ?o }} UNION {{ ?s ?p2 <{iri}> }} }}").askAnswer)
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
    """Return a blocking hint if the query targets a class the VoID does not
    describe, else None. Only project/domain IRIs are guarded — standard vocab
    (rdfs:/owl:/rdf:/xsd:) and the VoID inventory itself are never flagged."""
    if not sparql_query:
        return None
    inv = _inventory(endpoint_url)
    if not inv["iris"]:                      # no VoID inventory -> don't guard
        return None
    # VoID prefixes + prefixes the query declares (so a class written with an
    # inline cross-endpoint prefix still expands and gets checked).
    _qdecl = re.findall(r"(?im)^\s*PREFIX\s+([A-Za-z][\w.\-]*)\s*:\s*<([^>]+)>",
                        sparql_query)
    ns = {**namespaces(endpoint_url), **dict(_qdecl)}
    toks = {m.group(1) for m in _TYPE_POS.finditer(sparql_query)}
    toks |= {m.group(1) for m in _SUBJ_OF_SUBCLASS.finditer(sparql_query)}

    for tok in toks:
        iri = _expand(tok, ns)
        if not iri or any(iri.startswith(s) for s in _SKIP_NS):
            continue
        if iri in inv["iris"]:
            continue

        # The VoID is NOT the source of truth for "does this exist" — it is
        # deliberately incomplete (it catalogues the fq/futuram data classes but
        # NOT the ChEBI taxonomy or the criticality vocab the data also uses). So a
        # real class the query legitimately needs (e.g. the rare-earth group
        # CHEBI_33319) is absent from VoID yet present in the data. Blocking on VoID
        # alone would falsely reject those — and the rule is: when in doubt, ALLOW.
        # THE LIVE DATA is the authority. Block ONLY when an ASK proves the IRI
        # appears NOWHERE in the data (subject or object). A probe failure fails
        # OPEN (allow). This blocks invented classes (crit:CriticalRawMaterial →
        # absent) while always allowing anything the data actually contains.
        base = re.split(r"[#/]", iri)
        ns_base = iri[: len(iri) - len(base[-1])]
        if _exists_in_data(endpoint_url, iri):
            continue

        local = base[-1]
        cands = get_close_matches(local.lower(),
                                  list(inv["by_local"]), n=6, cutoff=0.4)
        hint_names = [inv["by_local"][c].split("#")[-1].split("/")[-1]
                      for c in cands]
        lbl_hits = [lbl for lbl in inv["by_label"] if local.lower() in lbl][:6]
        msg = [
            f"BLOCKED — the class <{iri}> is not in this endpoint's VoID, so the "
            f"query was NOT run (it would return nothing and waste a step).",
            "Do not invent or copy class IRIs. Resolve the right class from the VoID:",
            "  - call search_sparql_docs / get_classes_schema to read the classes, or",
            "  - find it by label: ?c rdfs:label ?l . FILTER(CONTAINS(LCASE(?l), \"<term>\")).",
        ]
        if hint_names:
            msg.append("Closest class names in the VoID: " + ", ".join(hint_names) + ".")
        if lbl_hits:
            msg.append("Classes whose label contains \"" + local + "\": "
                       + ", ".join(lbl_hits) + ".")
        # LIVE FALLBACK (only when VoID gave no good candidate): anchored on a
        # VoID-known parent of the same namespace, enumerate its actual
        # subclasses/instances so the model gets REAL usable names.
        if not hint_names and not lbl_hits:
            try:
                live = _live_candidates(endpoint_url, inv, local, ns_base)
            except Exception:  # noqa: BLE001
                live = []
            if live:
                msg.append("No VoID class matched by name/label; from the actual "
                           "data, classes under the related VoID-known class that "
                           "resemble \"" + local + "\": " + ", ".join(live) + ".")
            else:
                msg.append("No catalogued class resembles \"" + local
                           + "\" — re-read the schema for the correct term.")
        return "\n".join(msg)
    return None

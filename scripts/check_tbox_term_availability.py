#!/usr/bin/env python3
"""Check every non-futuram class/property referenced by ontology/tbox/*.ttl
resolves. Buckets each term as own (skipped), standard W3C, local-source (must be
in ontology/sources/*), or remote-only (fetched once, saved, verified). Exit 0 if all resolve.

Run (per repo policy, via uv + rdflib):

    uv run --with rdflib python scripts/check_tbox_term_availability.py

ChEBI term set is cached under .cache/. --refresh re-downloads/re-parses;
--offline skips network fetches (unsaved remote vocabs report UNVERIFIED).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from rdflib import Graph, RDF, RDFS, OWL, URIRef
from rdflib.namespace import XSD

REPO = Path(__file__).resolve().parent.parent
TBOX = REPO / "ontology" / "tbox"
SOURCES = REPO / "ontology" / "sources"
CACHE = REPO / ".cache" / "tbox_term_availability"
# Downloaded vocabularies become first-class, committable source files, each in
# its OWN folder under ontology/sources/ — exactly like chebi/, ceon/, emmo/.

# The ontology's own namespaces — referenced terms here are NOT checked.
OWN_NAMESPACES = (
    "https://www.purl.org/futuram#",
    "https://www.purl.org/futuram/query#",
)

# Standard vocabularies: resolvable by definition (W3C core RDF stack).
STANDARD_NAMESPACES = {
    str(OWL): "owl",
    str(RDFS): "rdfs",
    str(RDF): "rdf",
    str(XSD): "xsd",
}

# Source ontologies the local-source terms must be found in. (key -> file)
SOURCE_FILES = {
    "chebi": SOURCES / "chebi" / "chebi_core.owl",
    "ceon": SOURCES / "ceon" / "ceon-base.owl",
    "emmo": SOURCES / "emmo" / "emmo-full.ttl",
    "metal-wheel-tbox": SOURCES / "metal-wheel" / "MetalWheel-TBox.ttl",
    "metal-wheel-abox": SOURCES / "metal-wheel" / "MetalWheel-ABox.ttl",
    "metal-wheel-crit": SOURCES / "metal-wheel" / "MetalWheel-Criticality-ABox.ttl",
    "dqv": SOURCES / "_support" / "dqv.ttl",
    "prov-o": SOURCES / "_support" / "prov-o.ttl",
}

# Namespace -> local source key it must resolve in; absence is a hard MISS.
# The OBO namespace is routed by LOCAL_PREFIX_TO_SOURCE instead (it spans
# ChEBI/Apollo-SV/STATO, told apart by IRI prefix).
LOCAL_NS_TO_SOURCE = {
    "http://w3id.org/CEON/ontology/product/": ("ceon",),
    "http://w3id.org/CEON/ontology/statement/": ("ceon",),
    "http://w3id.org/CEON/ontology/material/": ("ceon",),
    "http://w3id.org/CEON/ontology/quantity/": ("ceon",),
    "http://w3id.org/CEON/ontology/resourceODP/": ("ceon",),
    "http://w3id.org/CEON/ontology/processODP/": ("ceon",),
    "http://w3id.org/CEON/ontology/process/": ("ceon",),
    "http://www.w3.org/ns/dqv#": ("dqv",),
    "http://www.w3.org/ns/prov#": ("prov-o",),
}

# IRI-PREFIX -> local source key, for namespaces shared by several ontologies.
# The OBO PURL hosts ChEBI (bundled), Apollo-SV and STATO (downloaded on first
# run) in ONE namespace, told apart only by the term-id prefix.
LOCAL_PREFIX_TO_SOURCE = {
    "http://purl.obolibrary.org/obo/CHEBI_": "chebi",
    "http://purl.obolibrary.org/obo/APOLLO_SV_": "apollo-sv",
    "http://purl.obolibrary.org/obo/STATO_": "stato",
    # IAO (Information Artifact Ontology) annotation properties, e.g.
    # IAO_0000115 "definition", are imported into every OBO ontology — resolve
    # them against ChEBI, the always-loaded OBO source.
    "http://purl.obolibrary.org/obo/IAO_": "chebi",
}

# Sources with NO bundled local copy — downloaded ONCE into ontology/sources/
# <key>/, then read like any other source. Each entry: source-key ->
# (relpath under SOURCES, [urls], force_format|None); joins SOURCE_FILES' pool.
REMOTE_SOURCE_FILES = {
    # OBO sub-ontologies sharing the obo: namespace but not bundled in ChEBI.
    "apollo-sv": (
        "apollo-sv/apollo_sv.owl",
        ["http://purl.obolibrary.org/obo/apollo_sv.owl"],
        "xml",  # served as text/plain; rdflib can't sniff it
    ),
    "stato": (
        "stato/stato.owl",
        ["http://purl.obolibrary.org/obo/stato.owl"],
        "xml",
    ),
    # The real EMMO — the bundled emmo/emmo-full.ttl is only a 27-triple stub
    # that lacks every opaque EMMO_* IRI the TBox references.
    "emmo-full-remote": (
        "emmo/emmo.ttl",
        ["https://emmo-repo.github.io/emmo.ttl"],
        "turtle",
    ),
    # Pure remote vocabularies (no source/ copy by design).
    "qudt": (
        "qudt/qudt-schema.ttl",
        ["https://qudt.org/3.1.4/schema/qudt",
         "https://qudt.org/schema/qudt/SCHEMA_QUDT-v2.1.ttl"],
        None,
    ),
    "w3c-time": (
        "w3c-time/w3c-time.ttl",
        ["http://www.w3.org/2006/time#", "https://www.w3.org/2006/time"],
        None,
    ),
    "skos": (
        "skos/skos.rdf",
        ["https://www.w3.org/2009/08/skos-reference/skos.rdf",
         "http://www.w3.org/2004/02/skos/core#"],
        None,
    ),
    "dcterms": (
        "dcterms/dcterms.ttl",
        ["https://www.dublincore.org/specifications/dublin-core/dcmi-terms/dublin_core_terms.ttl",
         "http://purl.org/dc/terms/"],
        None,
    ),
    "iof-core": (
        "iof-core/iof-core.rdf",
        ["https://spec.industrialontologies.org/ontology/core/Core/",
         "https://raw.githubusercontent.com/iofoundry/ontology/master/core/Core.rdf"],
        None,
    ),
}

# Namespace -> source-key(s) for remote-fetched vocabs; same contract as
# LOCAL_NS_TO_SOURCE. iof-core is spelled .../core/Core/ here but defined under
# .../construct/, so its membership check is by local NAME (see resolves_in).
REMOTE_NS_TO_SOURCE = {
    "https://w3id.org/emmo#": ("emmo-full-remote",),
    "http://qudt.org/schema/qudt/": ("qudt",),
    "http://www.w3.org/2006/time#": ("w3c-time",),
    "http://www.w3.org/2004/02/skos/core#": ("skos",),
    "http://purl.org/dc/terms/": ("dcterms",),
    "https://spec.industrialontologies.org/ontology/core/Core/": ("iof-core",),
}

# Predicates whose OBJECT is a term IRI we should resolve.
TERM_OBJECT_PREDICATES = {
    RDF.type,
    RDFS.subClassOf,
    RDFS.subPropertyOf,
    RDFS.domain,
    RDFS.range,
    RDFS.seeAlso,
    OWL.inverseOf,
    OWL.onProperty,
    OWL.someValuesFrom,
    OWL.allValuesFrom,
    OWL.equivalentClass,
    OWL.equivalentProperty,
    OWL.members,
    OWL.unionOf,
    OWL.intersectionOf,
    OWL.onClass,
    OWL.propertyChainAxiom,
}


def namespace_of(iri: str) -> str:
    """Namespace part of an IRI (up to and including the last # or /)."""
    if "#" in iri:
        return iri.rsplit("#", 1)[0] + "#"
    return iri.rsplit("/", 1)[0] + "/"


def collect_referenced_terms(path: Path) -> set[str]:
    """Every IRI used in a class/property/individual position: predicates,
    objects of term-defining predicates, and URIRefs in RDF list nodes.
    Subjects (declared here), literals and blank nodes are skipped."""
    g = Graph()
    g.parse(path, format="turtle")
    terms: set[str] = set()

    def walk_collection(node) -> None:
        # node is the head of an rdf:Collection (or a single URIRef)
        if isinstance(node, URIRef):
            terms.add(str(node))
            return
        for item in g.items(node):  # rdf:first/rdf:rest list
            if isinstance(item, URIRef):
                terms.add(str(item))

    # rdf:first / rdf:rest are list plumbing emitted by rdflib for every
    # Collection — they are not terms the ontology "references" in any
    # meaningful sense, so drop them from the predicate sweep.
    list_plumbing = {RDF.first, RDF.rest}
    for s, p, o in g:
        if isinstance(p, URIRef) and p not in list_plumbing:
            terms.add(str(p))
        if p in TERM_OBJECT_PREDICATES:
            if isinstance(o, URIRef):
                terms.add(str(o))
            else:  # blank node: may head a list (unionOf, members, chain, …)
                walk_collection(o)
    return terms


def load_defined_subjects(path: Path) -> set[str]:
    """Every IRI that appears as a subject (i.e. is *defined*) in a graph."""
    g = Graph()
    fmt = "xml" if path.suffix.lower() in {".owl", ".rdf", ".xml"} else None
    g.parse(path, format=fmt)
    return {str(s) for s in g.subjects() if isinstance(s, URIRef)}


def cached_subjects(key: str, path: Path, refresh: bool) -> set[str]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / f"{key}.json"
    if cache_file.exists() and not refresh:
        if cache_file.stat().st_mtime >= path.stat().st_mtime:
            return set(json.loads(cache_file.read_text()))
    print(f"  parsing {path.relative_to(REPO)} …", flush=True)
    subjects = load_defined_subjects(path)
    cache_file.write_text(json.dumps(sorted(subjects)))
    return subjects


def ensure_remote_source(key: str, refresh: bool, offline: bool):
    """Make sure a REMOTE_SOURCE_FILES entry is downloaded into its own folder
    under ontology/sources/, returning its saved Path (or None if it could not
    be obtained). Idempotent: the saved file IS the source on later runs."""
    relpath, urls, force_fmt = REMOTE_SOURCE_FILES[key]
    saved = SOURCES / relpath
    if saved.exists() and not refresh:
        return saved
    if offline:
        return None
    saved.parent.mkdir(parents=True, exist_ok=True)
    for url in urls:
        try:
            print(f"  fetching {key}: {url} …", flush=True)
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "text/turtle, application/rdf+xml;q=0.9, */*;q=0.5",
                    "User-Agent": "futuram-tbox-checker/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read()
            # validate it parses to a non-empty graph before saving
            g = Graph()
            for fmt in ([force_fmt] if force_fmt else _formats_for(url, ctype)):
                try:
                    g = Graph()
                    g.parse(data=body, format=fmt)
                    break
                except Exception:
                    g = Graph()
            if len(g) == 0:
                continue
            saved.write_bytes(body)
            print(f"    saved -> {saved.relative_to(REPO)} ({len(g)} triples)", flush=True)
            return saved
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            print(f"    ! {url}: {e}", flush=True)
            continue
    return None


def _formats_for(url: str, ctype: str) -> list[str]:
    c = ctype.lower()
    if "turtle" in c or url.endswith(".ttl"):
        return ["turtle", "xml"]
    if "xml" in c or url.endswith((".rdf", ".owl", ".xml")):
        return ["xml", "turtle"]
    return ["turtle", "xml", "n3"]


def local_name(iri: str) -> str:
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def expected_sources(iri: str) -> tuple[str, ...] | None:
    """The source-key(s) a term is expected to resolve in, by IRI prefix
    (most specific) then namespace. None if the term is in no mapped source."""
    for prefix, key in LOCAL_PREFIX_TO_SOURCE.items():
        if iri.startswith(prefix):
            return (key,)
    ns = namespace_of(iri)
    if ns in LOCAL_NS_TO_SOURCE:
        return LOCAL_NS_TO_SOURCE[ns]
    if ns in REMOTE_NS_TO_SOURCE:
        return REMOTE_NS_TO_SOURCE[ns]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true",
                    help="rebuild every cache (local parses + remote fetches)")
    ap.add_argument("--offline", action="store_true",
                    help="skip network fetches; remote-only terms => UNVERIFIED")
    args = ap.parse_args()

    tbox_files = sorted(TBOX.glob("*.ttl"))
    print(f"Discovered {len(tbox_files)} TBox files in {TBOX.relative_to(REPO)}/")

    # 1. collect referenced terms per TBox file
    print("\nCollecting referenced terms …")
    referenced: dict[str, set[str]] = {}
    for f in tbox_files:
        referenced[f.name] = collect_referenced_terms(f)
        print(f"  {f.name}: {len(referenced[f.name])} distinct terms referenced")
    all_terms: set[str] = set().union(*referenced.values())

    # 2. bucket each term
    own, standard, sourced, unknown = set(), set(), set(), set()
    for t in all_terms:
        ns = namespace_of(t)
        if ns in OWN_NAMESPACES:
            own.add(t)
        elif ns in STANDARD_NAMESPACES:
            standard.add(t)
        elif expected_sources(t) is not None:
            sourced.add(t)
        else:
            unknown.add(t)

    print(
        f"\nBuckets: {len(own)} own (skipped) | {len(standard)} standard | "
        f"{len(sourced)} sourced | {len(unknown)} UNMAPPED ns"
    )

    # 3. which source-keys do we actually need? load only those.
    needed_keys = set()
    for t in sourced:
        needed_keys.update(expected_sources(t))

    print("\nLoading source ontologies (downloading remote ones into ontology/sources/) …")
    source_subjects: dict[str, set[str]] = {}
    source_label: dict[str, str] = {}
    for key in sorted(needed_keys):
        if key in SOURCE_FILES:
            path = SOURCE_FILES[key]
            if not path.exists():
                print(f"  WARNING: source missing: {path.relative_to(REPO)}")
                continue
            source_subjects[key] = cached_subjects(key, path, args.refresh)
            source_label[key] = str(path.relative_to(REPO))
        elif key in REMOTE_SOURCE_FILES:
            saved = ensure_remote_source(key, args.refresh, args.offline)
            if saved is None:
                source_label[key] = "UNAVAILABLE (offline or download failed)"
                continue
            source_subjects[key] = cached_subjects(key, saved, args.refresh)
            source_label[key] = str(saved.relative_to(REPO))

    misses: list[tuple[str, str]] = []  # (term, reason)

    def resolves_in(iri: str, key: str) -> bool:
        """Is the term defined in source `key`? Exact-IRI match, with a
        local-NAME fallback for sources that publish the same term under a
        different module IRI (e.g. IOF Core's .../construct/ vs .../core/Core/)."""
        subs = source_subjects.get(key)
        if subs is None:
            return False
        if iri in subs:
            return True
        if key == "iof-core":  # IOF spells Core terms under .../construct/
            ln = local_name(iri)
            return any(local_name(s) == ln for s in subs)
        return False

    # 4. standard vocab — OK by definition
    print("\n=== Standard-vocabulary terms (resolvable by definition) ===")
    for t in sorted(standard):
        pfx = STANDARD_NAMESPACES[namespace_of(t)]
        print(f"  OK  {pfx}:{local_name(t)}")

    # 5. sourced terms — must resolve in their expected source
    print("\n=== Sourced terms (must resolve in ontology/sources/*) ===")
    for t in sorted(sourced):
        expected = expected_sources(t)
        hit = [k for k in expected if resolves_in(t, k)]
        if hit:
            via = ", ".join(f"{k} [{source_label.get(k, '?')}]" for k in hit)
            print(f"  OK   {t}\n         in: {via}")
        elif any(source_label.get(k, "").startswith("UNAVAILABLE") for k in expected):
            print(f"  UNVERIFIED {t}\n               source(s) {expected} unavailable")
            if not args.offline:
                misses.append((t, f"source unavailable: {expected}"))
        else:
            misses.append((t, f"absent from expected source {expected}"))
            print(f"  MISS {t}  (expected in {', '.join(expected)})")

    # 6. unmapped namespaces — scan every loaded source as a best effort
    if unknown:
        print("\n=== Terms in UNMAPPED namespaces (best-effort lookup) ===")
        for t in sorted(unknown):
            found = [k for k in source_subjects if t in source_subjects[k]]
            if found:
                print(f"  OK?  {t}  (found in {', '.join(found)})")
            else:
                misses.append((t, f"unmapped namespace {namespace_of(t)}, no source"))
                print(f"  MISS {t}  (unmapped ns: {namespace_of(t)})")

    # 7. summary
    print("\n" + "=" * 64)
    print("SUMMARY")
    print(f"  TBox files          : {len(tbox_files)}")
    print(f"  terms referenced    : {len(all_terms)}")
    print(f"  own (skipped)       : {len(own)}")
    print(f"  standard-vocab      : {len(standard)}")
    print(f"  sourced             : {len(sourced)}")
    print(f"  unmapped-ns         : {len(unknown)}")
    print(f"  UNRESOLVED          : {len(misses)}")
    if misses:
        print("\nUnresolved references:")
        for t, reason in misses:
            print(f"  - {t}\n      {reason}")
        return 1
    print("\nAll non-futuram terms resolve. ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())

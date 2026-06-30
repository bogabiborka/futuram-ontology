# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml", "owlrl", "pyshacl"]
# ///
"""corpus — digest a DIRECTORY of composition RDF (any origin) into a catalog of each
file's year range + whole/part CLASSES, routing each query to only the files that can
answer it. build_corpus writes catalog.json; CorpusRouter.files_for(years=, classes=).
"""
import hashlib
import json
import pathlib
import sys


from rdflib import Graph, RDF, RDFS

from common import pipeline
from common.vocab import FUT

CATALOG_NAME = "corpus-catalog.json"


def load_corpus(comp_dir):
    """Scan a DIRECTORY of composition RDF, yielding (source_id, graph) for the builder
    (which never sees disk). Each .ttl is one source (source_id = relative path with
    "/" -> "_") with its EM_SHARED_NAME sibling chemistry merged in; SKIP_NAMES skipped."""
    comp_dir = pathlib.Path(comp_dir)
    for f in sorted(comp_dir.rglob("*.ttl")):
        if f.name in pipeline.SKIP_NAMES or f.name.endswith("-served.ttl"):
            continue
        g = Graph()
        g.parse(str(f), format="turtle")
        shared = f.parent / pipeline.EM_SHARED_NAME
        if shared.exists():
            g.parse(str(shared), format="turtle")
        source_id = str(f.relative_to(comp_dir).with_suffix("")).replace("/", "_")
        yield source_id, g


def _file_sig(path):
    """Content signature (size + sha1) so the digest re-runs only when a file's
    bytes change — makes 'just drop a .ttl in' self-healing and git-friendly."""
    data = pathlib.Path(path).read_bytes()
    return {"size": len(data), "sha1": hashlib.sha1(data).hexdigest()}


def _local(iri):
    s = str(iri)
    return s.split("#")[-1] if "#" in s else s.split("/")[-1]


def _scan_file(ttl_path):
    """Read one composition RDF file -> its catalog entry: constituent CLASSES (each
    typed node's futuram class) and any years parsed from '<name>_<YYYY>' node labels
    (best-effort, for routing)."""
    g = Graph()
    g.parse(str(ttl_path), format="turtle")
    classes = set()
    years = set()
    for node, _, typ in g.triples((None, RDF.type, None)):
        if str(typ).startswith(str(FUT)):
            classes.add(_local(typ))
        lbl = g.value(node, RDFS.label)
        if lbl is not None:
            tail = str(lbl).rsplit("_", 1)[-1]
            if tail.isdigit() and len(tail) == 4:
                years.add(int(tail))
    # ANCESTOR classes reached via subclass_of (e.g. V0301030101 ⊑ elvBEV):
    # the served graph aggregates these parent classes too, so a query targeting
    # elvBEV must route to this file even though no NODE is rdf:type elvBEV.
    for sub, sup in g.subject_objects(RDFS.subClassOf):
        if str(sub).startswith(str(FUT)) and str(sup).startswith(str(FUT)):
            classes.add(_local(sub))
            classes.add(_local(sup))
    return {
        "file": pathlib.Path(ttl_path).name,
        "classes": sorted(classes),
        "years": sorted(years),
        "year_lo": min(years) if years else None,
        "year_hi": max(years) if years else None,
        "n_triples": len(g),
    }


# the reserved catalog/artefact filenames live in common.pipeline (shared by ETL
# and builder without a cross-layer import); re-exported here for etl callers.
_SKIP_NAMES = pipeline.SKIP_NAMES


def build_corpus(rdf_dir, pattern="*.ttl", catalog_name=CATALOG_NAME,
                 incremental=True):
    """Digest every composition RDF file in `rdf_dir` (recursively) into catalog.json.
    INCREMENTAL by default: an unchanged file (same size+sha1) is reused, so dropping
    in ONE new .ttl only scans that file. Skips TBox/shape/served files."""
    rdf_dir = pathlib.Path(rdf_dir)
    cat_path = rdf_dir / catalog_name
    prev = {}
    if incremental and cat_path.exists():
        try:
            old = json.loads(cat_path.read_text())
            prev = {e.get("path", e["file"]): e for e in old.get("files", [])}
        except Exception:                           # noqa: BLE001
            prev = {}

    entries = []
    for f in sorted(rdf_dir.rglob(pattern)):
        if (f.name in _SKIP_NAMES or f.name.endswith("-served.ttl")
                or f.name == catalog_name):
            continue
        rel = str(f.relative_to(rdf_dir))
        try:
            sig = _file_sig(f)
            old = prev.get(rel)
            if old and old.get("sig") == sig:        # unchanged -> reuse
                entries.append(old)
                continue
            entry = _scan_file(f) | {"path": rel, "sig": sig}
            entries.append(entry)
        except Exception as e:                       # noqa: BLE001
            entries.append({"file": f.name, "path": rel, "error": str(e)[:120]})
    catalog = {"root": str(rdf_dir), "files": entries}
    cat_path.write_text(json.dumps(catalog, indent=2))
    return catalog


class CorpusRouter:
    """Route a query to the corpus files that can answer it (by year and/or
    whole/part class) — "know which RDF file to load given the whole-part [and
    year] of the query"."""

    def __init__(self, catalog_path, auto_digest=True):
        self.root = pathlib.Path(catalog_path).parent
        # Self-heal: re-digest (incrementally) on load so a newly dropped-in .ttl
        # is picked up with no manual step — "just add a new rdf does the work".
        if auto_digest:
            self.catalog = build_corpus(self.root,
                                        catalog_name=pathlib.Path(catalog_path).name)
        else:
            self.catalog = json.loads(pathlib.Path(catalog_path).read_text())
        self.files = [e for e in self.catalog["files"] if "error" not in e]

    def files_for(self, years=None, classes=None):
        """Absolute paths of corpus files matching the year window AND covering at
        least one of `classes`. A file with NO year info matches any year query
        (it isn't year-scoped). No constraints -> every file."""
        if isinstance(years, int):
            years = {years}
        want_classes = set(classes) if classes else None
        out = []
        for e in self.files:
            if years is not None and e.get("years"):
                if not (set(years) & set(range(e["year_lo"], e["year_hi"] + 1))):
                    continue
            if want_classes is not None and not (want_classes & set(e["classes"])):
                continue
            out.append(self.root / e.get("path", e["file"]))
        return out

    def all_files(self):
        return [self.root / e.get("path", e["file"]) for e in self.files]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Digest a directory of composition RDF")
    ap.add_argument("rdf_dir", type=pathlib.Path)
    args = ap.parse_args(argv)
    cat = build_corpus(args.rdf_dir)
    ok = [e for e in cat["files"] if "error" not in e]
    print(f"digested {len(ok)} RDF files -> {args.rdf_dir / CATALOG_NAME}")
    for e in ok:
        yr = f"{e['year_lo']}-{e['year_hi']}" if e.get("year_lo") else "no-year"
        print(f"  {e['file']}: {len(e['classes'])} classes, {yr}, {e['n_triples']} triples")


if __name__ == "__main__":
    main()

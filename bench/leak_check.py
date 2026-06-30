# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib"]
# ///
"""bench/leak_check.py — automated benchmark-leak detector.

The bench measures whether an LLM can RESOLVE the user's term to the right class
and read its composition. Anything we put in front of the model — the run_bench
SYSTEM PROMPT, the corrective RE-PROMPTS, and every bench/skills/*.md — must NOT
hand it a concrete DATA class (elvDiesel, Aluminium, a V-code, a segment/axis word),
a ground-truth VALUE, or the term→class resolution it is meant to derive. Naming any
of those leaks the answer.

This script harvests the leak VOCABULARY from the live ontology/served data
(EVERY futuram class local-name + its rdfs:label words) and scans every LLM-facing
text for a hit, minus an ALLOWLIST of schema terms a query legitimately names
(the level roots, the unknown KINDS, and the fq:/futuram: PROPERTY names — not data
classes). It also flags axis words and the SI golden values directly.

Run standalone:   uv run --offline --with rdflib bench/leak_check.py
Or via pytest:    it is imported by tests/test_bench_no_leaks.py
Exit non-zero (and pytest fails) if any leak is found; prints file:line + the hit.
"""
from __future__ import annotations

import pathlib
import re
import sys

from rdflib import Graph, RDF, RDFS, OWL, URIRef

REPO = pathlib.Path(__file__).resolve().parent.parent
FUT = "https://www.purl.org/futuram#"
SERVED = REPO / "fuseki" / "futuram" / "data" / "query" / "futuram.ttl"

# ---- the surfaces the LLM can see -------------------------------------------
SKILLS_DIR = REPO / "bench" / "skills"
# Every module that holds LLM-facing prompt text. run_bench.py is now a thin CLI;
# the system prompt + backend hints live in benchlib/prompts.py and the corrective
# re-prompt notes in benchlib/agent.py. ALL of them must be scanned, or a leak
# could slip in via a string that no longer lives in run_bench.py.
PROMPT_SOURCES = [
    REPO / "bench" / "run_bench.py",
    REPO / "bench" / "benchlib" / "prompts.py",
    REPO / "bench" / "benchlib" / "agent.py",
    # pre-exec guards emit corrective messages STRAIGHT into the LLM's context —
    # scan their string literals too so a guard can't leak a class/value.
    *sorted((REPO / "bench" / "helpers").glob("*.py")),
]


# ---- ALLOWLIST: schema vocabulary a query legitimately names ----------------
# These are NOT data/decoy classes — they are the fixed ontology SCHEMA a SPARQL
# query must reference, so naming them in a skill/prompt is not a leak.
ALLOW_CLASSES = {
    # the four composition-level roots
    "Product", "Component", "Material", "Element",
    # the four unattributed-remainder KIND classes (schema, not data)
    "unknownProduct", "unknownComponent", "unknownMaterial", "unknownElement",
    # generic OWL/RDF/QUDT terms a query uses
    "Class", "Amount", "Quantity",
    # uncertainty-method SCHEMA the calculate-uncertainty skill must name (the
    # ruleset + its distribution/combination vocabulary — not data classes)
    "UncertaintyRuleset", "RectangularDistribution", "RootSumOfSquares",
    "QuantityInterval", "DqsBand",
}
# fq:/futuram: PROPERTY local-names a query must use are allowed wholesale (a
# property is not a data class). Harvested from the data as predicates.
def _allowed_property_localnames(g):
    props = set()
    for p in set(g.predicates(None, None)):
        if str(p).startswith(FUT) or str(p).startswith(FUT.replace("#", "/query#")):
            props.add(str(p).split("#")[-1])
    return props


# ---- AXIS / segment / fleet words: never name the slice axes or body kinds ---
# These are the dimensions the model must infer; naming one leaks the resolution.
AXIS_WORDS = [
    r"\bproduction year\b", r"\byear[- ]slice\b", r"\bdrivetrain\b",
    # the body/size axis the model must infer — naming it (even generically as
    # "segment" or "size") hands over the resolution dimension the bench measures.
    r"\bbody segment\b", r"\bsegment\b", r"\bsegments\b", r"\bsize\b",
    r"\bsupermini\b", r"\bhatchback\b", r"\bfleet\b",
]
# ---- DOMAIN NOUNS: skills must speak at the TYPE / schema level (class, whole,
# group, constituent, component, member, subtree), never name a concrete domain
# thing. Naming the domain ("vehicle", "alloy", "motor", a concrete material) tells
# the model WHAT the data is about — a spoiler that defeats the resolve-by-label
# task. The model must discover the domain from the data, not be told it.
DOMAIN_WORDS = [
    r"\bvehicles?\b", r"\bcars?\b", r"\balloys?\b", r"\bmotors?\b",
    r"\bwiring\b", r"\bdrivetrains?\b",
    # NOTE: "engine" (SPARQL engine) and "harness" (test harness) are deliberately
    # NOT here — they are tooling vocabulary, not the data domain.
]
# Files allowed to name domain things because they document a NAMED bridged overlay
# the model must actually reference by name (the proper noun IS the schema vocab).
DOMAIN_WORD_EXEMPT_FILES = {
    "metal-wheel-recovery-criticality.md",  # the Metal-Wheel recovery overlay
    "chebi-element-classification.md",      # the ChEBI chemistry bridge
}
# Phrases that teach the model BAD habits — banned regardless of file.
# Each entry is a regex fragment; a match anywhere in an LLM-facing file is an error.
BANNED_PHRASES = [
    # Telling the model to match class IRIs by their local-name is wrong — local names
    # are opaque identifiers, not meaningful labels. Always resolve via rdfs:label.
    r"IRI\s+LOCAL.NAME",
    r"class\s+IRI\s+local.name",
    r"local.name.*rarely matches",
    r"labels here are mechanical",
    r"switch to matching.*local.name",
    r"abbreviations/acronyms.*labels",
]

# The SI golden values (the actual answers) must never appear verbatim.
GOLDEN_VALUES = [
    "113.05", "349.27", "176.16", "29.74", "7.57", "31.185", "10.43", "3.97",
    "66.52", "11.75", "1.94", "24.98", "11.90", "11.58", "0.980439",
]


# Generic words that, on their own, carry no class identity — a label is only a leak
# via its DISTINCTIVE words, so these are stripped before forming a label PHRASE.
_LABEL_STOPWORDS = {
    "vehicle", "production", "class", "segment", "battery", "electric", "diesel",
    "petrol", "hybrid", "standard", "small", "medium", "large", "mini", "family",
    "year", "the", "and", "for", "with", "from", "into", "unspecified", "other",
    "in",
}


def _label_phrase(lbl: str) -> str | None:
    """The distinctive multi-word core of a class rdfs:label, lowercased and
    space-normalised (e.g. 'embedded electronics — 2025 production year' ->
    'embedded electronics'). Returns None when fewer than 2 distinctive words remain
    (a single distinctive word is too generic / too false-positive-prone to flag as a
    phrase; the local-name + axis-word checks still cover those)."""
    # cut at an em/en-dash separator that introduces the year/production suffix
    head = re.split(r"[—–]", lbl)[0]
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9-]*", head)]
    distinctive = [w for w in words if w not in _LABEL_STOPWORDS and len(w) >= 3]
    if len(distinctive) < 2:
        return None
    return " ".join(distinctive)


def _harvest_data_class_terms(g):
    """Every futuram class local-name + the distinctive multi-word PHRASE of its
    rdfs:label, MINUS the allowlist. A hit on either in an LLM-facing text is a leak."""
    localnames = set()
    label_phrases = set()
    for s in g.subjects(RDF.type, OWL.Class):
        if not str(s).startswith(FUT):
            continue
        ln = str(s).split("#")[-1]
        if ln in ALLOW_CLASSES:
            continue
        localnames.add(ln)
        lbl = g.value(s, RDFS.label)
        if lbl:
            ph = _label_phrase(str(lbl))
            if ph:
                label_phrases.add(ph)
    return localnames, label_phrases


def scan_text(text, localnames, allowed_props, label_phrases=(), domain_words=True):
    """Return list of (kind, hit) leaks in `text`. `domain_words=False` skips the
    domain-noun check (for a file that legitimately names a bridged overlay)."""
    hits = []
    # 1. concrete data-class local-names (whole-word, case-sensitive — IRIs are)
    for ln in localnames:
        if ln in allowed_props:
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(ln)}(?![A-Za-z0-9])", text):
            hits.append(("data-class", ln))
    # 1b. concrete data-class LABEL PHRASES (case-insensitive; the distinctive words
    # of a class label, in order, allowing any run of non-word chars — space, hyphen —
    # between them, so "embedded electronics" / "embedded-electronics" both match the
    # 'embedded electronics' label core). This catches a label leaked as prose.
    for ph in label_phrases:
        pat = r"(?<![A-Za-z0-9])" + r"[^A-Za-z0-9]+".join(
            re.escape(w) for w in ph.split()) + r"(?![A-Za-z0-9])"
        if re.search(pat, text, re.I):
            hits.append(("data-class-label", ph))
    # 2. axis / segment / fleet words
    for pat in AXIS_WORDS:
        m = re.search(pat, text, re.I)
        if m:
            hits.append(("axis-word", m.group(0)))
    # 2b. concrete domain nouns (the model must discover the domain, not be told it)
    if domain_words:
        for pat in DOMAIN_WORDS:
            m = re.search(pat, text, re.I)
            if m:
                hits.append(("domain-word", m.group(0)))
    # 3. golden values
    for v in GOLDEN_VALUES:
        if re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", text):
            hits.append(("golden-value", v))
    # 4. banned phrases (bad advice that teaches wrong habits)
    for pat in BANNED_PHRASES:
        m = re.search(pat, text, re.I)
        if m:
            hits.append(("banned-phrase", m.group(0)))
    return hits


def _llm_facing_strings_from_source(src):
    """The strings the LLM sees: the SYSTEM PROMPT and every reprompt_note. We
    scan the source's string literals that look prompt-like (contain ANSWER /
    REJECTED / 'You are' / endpoint / labels)."""
    import ast
    out = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if len(s) > 30 and any(k in s for k in
                                   ("ANSWER", "REJECTED", "You are", "endpoint",
                                    "labels", "uncertaint", "SPARQL", "BLOCKED",
                                    "forbidden", "rdfs:", "owl:", "fq:", "class")):
                out.append((node.lineno, s))
    return out


# The always-on system prompt + backend hints (benchlib/prompts.py) are STRICTER
# than the rest: they must reveal NOTHING the model is meant to discover from the
# VoID/skills — not even a "schema" predicate (fq:contains, futuram:hasComposition…)
# and not a single line of SPARQL. The model gets the vocabulary by querying the
# VoID, never pre-baked into its system prompt. So for this one file we flag any
# served prefixed-name predicate and any SPARQL syntax, IGNORING the property
# allowlist that applies elsewhere.
_PROMPT_STRICT_SOURCE = REPO / "bench" / "benchlib" / "prompts.py"

# Signals of ACTUAL SPARQL (not the word "select" in prose): a ?variable, a
# bracketed property-list `[ pfx:` , a SELECT/CONSTRUCT immediately followed by a
# projection, or a property path on rdfs:subClassOf. Bare keywords in prose and the
# template's own {placeholder} braces are intentionally NOT matched.
_SPARQL_SYNTAX = re.compile(
    r"\?[a-z]\w*"                                  # a ?variable
    r"|\[\s*\w+:\w+"                               # a [ prefix:local property list
    r"|\b(?:SELECT|CONSTRUCT|ASK)\s+[\(\?\*]"      # SELECT (?x / SELECT ?x / SELECT *
    r"|\brdfs:subClassOf\b"                        # the subclass property/path
    r"|\bGROUP\s+BY\b|\bHAVING\s*\(")
# A prefixed name pointing at the served vocab (fq:foo, futuram:foo, qudt:foo), the
# bare prefix-with-placeholder (futuram:...), or the served namespace URL itself —
# any of these in the system prompt is a leaked predicate/class/namespace.
_SERVED_QNAME = re.compile(
    r"\b(?:fq|futuram|qudt):(?:[A-Za-z]\w+|\.\.\.)"
    r"|purl\.org/futuram")


def _strict_prompt_leaks():
    """SPARQL-syntax / served-qname leaks in the always-on prompt file, with NO
    property-allowlist exemption."""
    import ast
    out = []
    src = _PROMPT_STRICT_SOURCE.read_text()
    rel = _PROMPT_STRICT_SOURCE.relative_to(REPO).as_posix()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            for m in _SPARQL_SYNTAX.finditer(s):
                out.append((rel, node.lineno, "sparql-in-prompt", m.group(0)))
            for m in _SERVED_QNAME.finditer(s):
                out.append((rel, node.lineno, "served-vocab-in-prompt", m.group(0)))
    return out


def find_leaks():
    g = Graph().parse(str(SERVED))
    localnames, label_phrases = _harvest_data_class_terms(g)
    allowed_props = _allowed_property_localnames(g) | ALLOW_CLASSES
    leaks = []
    leaks += _strict_prompt_leaks()

    # skills
    for f in sorted(SKILLS_DIR.glob("*.md")):
        text = f.read_text()
        dw = f.name not in DOMAIN_WORD_EXEMPT_FILES
        for kind, hit in scan_text(text, localnames, allowed_props, label_phrases,
                                   domain_words=dw):
            # report the first line the hit appears on
            line = next((i + 1 for i, ln in enumerate(text.splitlines())
                         if hit in ln), 0)
            leaks.append((f"bench/skills/{f.name}", line, kind, hit))

    # bench prompts + re-prompts (run_bench CLI, benchlib/prompts, benchlib/agent)
    for path in PROMPT_SOURCES:
        if not path.exists():
            continue
        rel = path.relative_to(REPO).as_posix()
        for lineno, s in _llm_facing_strings_from_source(path.read_text()):
            for kind, hit in scan_text(s, localnames, allowed_props, label_phrases):
                leaks.append((rel, lineno, kind, hit))

    # SERVED rdfs:comment values — the BUILDER-DERIVED semantic comments the LLM
    # reads via SPARQL (CommentPlugin). These must not leak an AXIS word or a GOLDEN
    # value. We do NOT flag data-class local-names here (a comment legitimately may
    # mention a property), nor the source V-code rdfs:labels (real data the model is
    # meant to read) — only the authored-by-us comment text, for axis/golden leaks.
    for s in g.objects(None, RDFS.comment):
        text = str(s)
        # only the builder-derived class comments (our wording), not TBox property
        # comments (schema docs) — ours start with these stems.
        if not re.match(r"(Derived|A specific|Taxonomic)", text):
            continue
        for pat in AXIS_WORDS:
            m = re.search(pat, text, re.I)
            if m:
                leaks.append(("served:rdfs:comment", 0, "axis-word", m.group(0)))
        for v in GOLDEN_VALUES:
            if re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", text):
                leaks.append(("served:rdfs:comment", 0, "golden-value", v))

    return leaks


def main():
    leaks = find_leaks()
    if not leaks:
        print("LEAK CHECK: clean — no data-class / axis / golden-value leaks in any "
              "LLM-facing skill or prompt.")
        return 0
    print(f"LEAK CHECK: {len(leaks)} leak(s) found:\n")
    for path, line, kind, hit in sorted(leaks):
        print(f"  {path}:{line}  [{kind}]  {hit!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

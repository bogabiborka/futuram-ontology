"""Run the sparql-llm MCP server with SKILLS added (no upstream edit), exposed as
@mcp.prompt (one per skill) AND list_skills/get_skill tools (for a loop that
doesn't consume MCP prompts). Skills = every `<id>.md` in BENCH_SKILLS_DIR.
"""
import os
import pathlib
import sys

from mcp.server.fastmcp.prompts import base
from sparql_llm.mcp_server import mcp  # the already-configured FastMCP instance

# Retarget the upstream SIB/bioinformatics mcp.instructions blurb to this KB.
# Domain-neutral wording, no concrete class/value (which would leak the benchmark).
try:
    mcp.instructions = (
        "Provides tools to query the composition of products — which materials, "
        "components and elements they contain, and in what amounts — over the "
        "connected SPARQL endpoint(s), using SPARQL.")
except Exception:
    pass

SKILLS_DIR = pathlib.Path(os.getenv("BENCH_SKILLS_DIR", "/app/bench/skills"))


# Map an endpoint URL to its backend id, so a skill can be scoped to one backend
# and NEVER shown to the other. The composition model must never see the fq:
# vocabulary — not in docs, not in examples, and not in skills.
_ENDPOINT_BACKEND = {
    "http://localhost:47040/query/sparql": "fq",
    "http://localhost:47040/composition/sparql": "composition",
}


def _backend_of(endpoint_url):
    if not endpoint_url:
        return None
    if endpoint_url in _ENDPOINT_BACKEND:
        return _ENDPOINT_BACKEND[endpoint_url]
    # be liberal about host/port differences — key on the path segment
    if "/query/" in endpoint_url:
        return "fq"
    if "/composition/" in endpoint_url:
        return "composition"
    return None


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split an Agent-Skills SKILL.md into (frontmatter dict, markdown body). A skill
    file MAY open with a `---`-delimited YAML frontmatter block (per the Agent Skills
    spec: name/description/metadata/…). Returns ({}, text) when there is none."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    # find the closing '---' (first line is the opening one)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_text = "".join(lines[1:i])
            body = "".join(lines[i + 1:]).lstrip("\n")
            try:
                import yaml
                fm = yaml.safe_load(fm_text) or {}
                if not isinstance(fm, dict):
                    fm = {}
            except Exception:
                fm = {}
            return fm, body
    return {}, text


def _parse_backends(fm: dict, body: str) -> set | None:
    """The set of backend ids a skill applies to, or None = ALL backends (agnostic).
    Prefer the spec frontmatter `metadata.backends`; fall back to a legacy
    `<!-- backends: ... -->` header tag for any not-yet-migrated skill."""
    meta = fm.get("metadata") or {}
    raw = meta.get("backends") if isinstance(meta, dict) else None
    if raw:
        if isinstance(raw, str):
            raw = raw.replace(",", " ").split()
        return {str(b).strip() for b in raw if str(b).strip()}
    for ln in body.splitlines()[:6]:
        m = ln.strip()
        if m.lower().startswith("<!-- backends:") and m.endswith("-->"):
            inner = m[len("<!-- backends:"):-len("-->")].strip()
            return {b.strip() for b in inner.replace(",", " ").split() if b.strip()}
    return None


def _load_skills() -> dict[str, dict]:
    """id -> {summary, body, backends} for every <id>.md in SKILLS_DIR.
    The summary (shown in list_skills / as the @mcp.prompt description) is the skill's
    frontmatter `description` — the 'what it does + when to use' discovery text — with
    a fallback to the first heading for any skill still lacking frontmatter.
    `backends` is a set of backend ids the skill is allowed for, or None=all."""
    out: dict[str, dict] = {}
    if not SKILLS_DIR.is_dir():
        return out
    for f in sorted(SKILLS_DIR.glob("*.md")):
        text = f.read_text()
        fm, body = _split_frontmatter(text)
        summary = (fm.get("description")
                   or next((ln.strip("# ").strip()
                            for ln in body.splitlines()
                            if ln.strip() and not ln.strip().startswith("<!--")), f.stem))
        out[f.stem] = {"summary": summary.strip(), "body": body,
                       "backends": _parse_backends(fm, body)}
    return out


SKILLS = _load_skills()


def _skills_for(endpoint_url) -> dict:
    """The subset of skills allowed for the backend behind endpoint_url (no-tag
    skill applies to all). Unknown endpoint -> ALL skills (the harness force-pins
    endpoint_url, so this default is safe only for non-bench callers)."""
    backend = _backend_of(endpoint_url)
    if backend is None:
        return SKILLS
    return {sid: s for sid, s in SKILLS.items()
            if s["backends"] is None or backend in s["backends"]}


# --- Dynamic, VoID-DERIVED "prefixes" skill --------------------------------- #
# Canonical PREFIX declarations read from the endpoint's VoID (never hardcoded),
# backend-scoped through endpoint_url so composition never sees the fq: namespace.
import json as _json

# The VoID Turtle for each backend, as mounted INSIDE the MCP container by
# docker-compose.bench.yml. (settings.bench.json is not mounted here, but the VoID
# files are — and the MCP already keys endpoints by backend.) Overridable via env.
_VOID_FILES = {
    "fq": os.getenv("BENCH_FQ_VOID", "/app/sparql-llm/futuram_void.ttl"),
    "composition": os.getenv("BENCH_COMPOSITION_VOID",
                             "/app/sparql-llm/futuram_void_composition.ttl"),
}
_PREFIX_CACHE: dict = {}
_PREFIXES_SKILL_ID = "prefixes"
_PREFIXES_SUMMARY = ("the EXACT prefix declarations for this endpoint, from its "
                     "VoID — paste these; do not guess a namespace")


def _void_prefixes(endpoint_url) -> dict:
    """{prefix: namespace} from the endpoint's VoID Turtle, keyed by backend.
    Cached. Empty if unavailable."""
    if endpoint_url in _PREFIX_CACHE:
        return _PREFIX_CACHE[endpoint_url]
    ns = {}
    backend = _backend_of(endpoint_url)
    vf = _VOID_FILES.get(backend)
    try:
        if vf and pathlib.Path(vf).exists():
            from rdflib import Graph
            g = Graph().parse(vf, format="turtle")
            for pfx, uri in g.namespaces():
                if pfx:
                    ns[str(pfx)] = str(uri)
    except Exception:
        pass
    _PREFIX_CACHE[endpoint_url] = ns
    return ns


def _prefixes_skill_body(endpoint_url) -> str:
    ns = _void_prefixes(endpoint_url)
    if not ns:
        return ("# Skill — prefixes for this endpoint\n\n"
                "No VoID prefix list available; read the schema with "
                "search_sparql_docs and copy prefixes from the examples verbatim.")
    decls = "\n".join(f"PREFIX {p}: <{u}>" for p, u in sorted(ns.items()))
    return ("# Skill — prefixes for THIS endpoint (from its VoID)\n\n"
            "Use these EXACT prefix declarations. Do not invent or alter a "
            "namespace (a wrong scheme like http:// vs https://, a missing `www.`, "
            "or a trailing `/` vs `#` will silently return no results). Paste the "
            "ones you need at the top of every query:\n\n```sparql\n"
            + decls + "\n```")


def _register_prompts() -> list[str]:
    names = []
    for skill_id, s in SKILLS.items():
        name = f"skill_{skill_id}"

        def _make(body: str):
            def _prompt() -> list[base.Message]:
                return [base.UserMessage(body)]
            return _prompt

        fn = _make(s["body"])
        fn.__name__ = name
        fn.__doc__ = s["summary"]
        mcp.prompt(name=name, description=s["summary"])(fn)
        names.append(name)
    return names


def _register_tools() -> None:

    @mcp.tool()
    def list_skills(endpoint_url: str | None = None) -> str:
        """List the available SKILLS (named how-to procedures) you can fetch with
        get_skill. Call this first when a question needs a method you are unsure of.
        Pass the endpoint_url you are querying so only THAT backend's skills show."""
        skills = _skills_for(endpoint_url)
        lines = [f"- {_PREFIXES_SKILL_ID}: {_PREFIXES_SUMMARY}"]
        lines += [f"- {sid}: {s['summary']}" for sid, s in skills.items()]
        return ("Available skills (call get_skill(skill_id) for the full "
                "procedure):\n" + "\n".join(lines))

    _register_class_candidates_tool()

    @mcp.tool()
    def get_skill(skill_id: str, endpoint_url: str | None = None) -> str:
        """Return the full step-by-step procedure for a skill_id from list_skills;
        use it to write your SPARQL. Pass the endpoint_url you are querying; a skill
        not applying to that backend is not returned (its vocab wouldn't exist)."""
        sid0 = skill_id.removeprefix("skill_")
        if skill_id == _PREFIXES_SKILL_ID or sid0 == _PREFIXES_SKILL_ID:
            return _prefixes_skill_body(endpoint_url)
        skills = _skills_for(endpoint_url)
        sid = skill_id if skill_id in skills else skill_id.removeprefix("skill_")
        s = skills.get(sid)
        if not s:
            # Distinguish "exists but not for your backend" from "no such skill".
            if (SKILLS.get(skill_id) or SKILLS.get(skill_id.removeprefix("skill_"))):
                return (f"Skill '{skill_id}' does not apply to this endpoint and "
                        f"is not available here. Use one of: {', '.join(skills)}.")
            return (f"No skill '{skill_id}'. Available: "
                    f"{', '.join(skills) or '(none)'}")
        return s["body"]


def _register_class_candidates_tool() -> bool:
    """Add `find_candidate_classes(term)` — a SEMANTIC class resolver. It embeds the
    user's plain-language term and returns the nearest CLASSES by their label+comment
    meaning (indexed by bench/index_classes.py into the `futuram_classes` collection),
    each as {iri, label, comment, score}. This turns class discovery from dozens of
    blind SPARQL probes into one vector lookup. Leak-safe: class labels/comments carry
    no numeric values, so a candidate names a class (legitimate resolution) but never a
    data value. The model STILL must verify the IRI and query it — these are only
    suggestions. No-op (returns False) if the collection isn't indexed yet."""
    collection = os.getenv("BENCH_CLASS_COLLECTION", "futuram_classes")
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        from sparql_llm.mcp_server import embedding_model, qdrant_client
        # only register if the collection exists (indexer has run)
        if not qdrant_client.collection_exists(collection):
            print(f"[bench] find_candidate_classes DISABLED — collection "
                  f"'{collection}' not found (run bench/index_classes.py).")
            return False
    except Exception as e:  # noqa: BLE001
        print(f"[bench] find_candidate_classes DISABLED — {e!r}")
        return False

    # A standing reminder appended to every candidate list: the search returns a
    # SIMILARITY-RANKED list, which is an INCOMPLETE slice of any group — so a
    # "for each X" / "distribution / total over a kind" question must be answered by
    # walking the rdfs:subClassOf HIERARCHY with SPARQL (the model's own query), not
    # by hand-listing whichever siblings this search happened to surface. The tool
    # deliberately does NOT compute the hierarchy for the model — discovering it via
    # SPARQL is the skill under test.
    _SEED_NOT_SET_NOTE = (
        "\n\nNOTE — this is a SIMILARITY-ranked SEED, not a complete set. Several of "
        "the classes above are usually siblings under ONE rdfs:subClassOf parent, and "
        "the search returns only the nearest few — never the whole group. If the "
        "question ranges over a group (\"for EACH …\", \"the distribution of …\", a "
        "TOTAL over a kind), do NOT hand-list these candidates. Instead use SPARQL to "
        "walk the hierarchy yourself: take one candidate, find its rdfs:subClassOf "
        "parent (the non-level-root one), then `SELECT ?sub WHERE { ?sub "
        "rdfs:subClassOf <thatParent> }` for the COMPLETE set, and compute every "
        "subclass's value in that query. These candidates only reveal that the parent "
        "exists — the parent's subclasses are the answer.")

    @mcp.tool()
    def find_candidate_classes(term: str, endpoint_url: str | None = None,
                               limit: int = 25) -> str:
        """Resolve a plain-language term (e.g. 'the drive motor', 'a diesel passenger
        car', 'copper wiring') to the BEST-FITTING CLASSES by SEMANTIC similarity of
        their rdfs:label + rdfs:comment. Returns up to `limit` candidates (default 25 —
        deliberately generous so ONE call shows you the whole neighbourhood and you
        never need to re-search the same term), each with its full class IRI, label,
        and comment. Call this ONCE per term: read the ranked list, pick the IRI whose
        label/comment best fits, and COMMIT to it — then verify by querying that exact
        IRI. Do NOT re-call this for the same term hoping for a different list, and do
        NOT string-match the IRI yourself. Pass the endpoint_url you are querying so
        only that backend's classes are returned."""
        ep_cond = ([FieldCondition(key="endpoint_url",
                                   match=MatchValue(value=endpoint_url))]
                   if endpoint_url else [])
        try:
            emb = next(iter(embedding_model.embed([term])))
            hits = qdrant_client.query_points(
                query=emb, collection_name=collection, limit=max(1, min(limit, 50)),
                query_filter=Filter(must=ep_cond) if ep_cond else None).points
        except Exception as e:  # noqa: BLE001
            return f"class-candidate search failed: {e}"
        if not hits:
            return (f"No candidate classes matched '{term}'. Try a synonym or a more "
                    "general word, or search rdfs:label/rdfs:comment via SPARQL.")
        lines = [f"Candidate classes for '{term}' (ranked by label+comment meaning — "
                 "verify the right one by querying its IRI, do NOT string-match it):"]
        iris = []
        for h in hits:
            p = h.payload or {}
            iri = p.get("iri")
            if iri:
                iris.append(iri)
            lines.append(
                f"\n- IRI:     {iri}"
                f"\n  label:   {p.get('label') or '(none)'}"
                f"\n  comment: {p.get('comment') or '(none)'}"
                f"\n  score:   {round(float(h.score), 3)}")
        out = "\n".join(lines)
        # Remind the model: this is a similarity SEED, not a complete group — for a
        # "for each / total over a kind" question, walk the rdfs:subClassOf hierarchy
        # in SPARQL rather than hand-listing the candidates surfaced here.
        if len(iris) >= 3:
            out += _SEED_NOT_SET_NOTE
        return out

    print(f"[bench] find_candidate_classes ENABLED (collection '{collection}').")
    return True


def _register_search_override() -> bool:
    """Replace search_sparql_docs with one that FILTERS retrieved docs to the queried
    endpoint (both backends share one Qdrant collection, so without it a composition
    run retrieves fq examples). Set BENCH_FILTER_DOCS_BY_ENDPOINT=0 to disable."""
    if os.getenv("BENCH_FILTER_DOCS_BY_ENDPOINT", "1") == "0":
        return False
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from sparql_llm.mcp_server import (
        embedding_model, qdrant_client, settings, format_docs,
        SEARCH_SPARQL_DOCS_TOOL_DESC,
    )
    # Override the upstream BIOLOGICAL-data PROMPT_TOOL_SPARQL with a domain-neutral
    # version (same {docs_count}/{formatted_docs} contract); no concrete class/value
    # named (that would leak the benchmark).
    PROMPT_TOOL_SPARQL = (
        "Formulate a precise SPARQL query to answer the user's question against "
        "the connected SPARQL endpoint(s).\n\n"
        "## SPARQL Query Guidelines\n"
        "- **Always include the endpoint URL** as a comment at the start: "
        "`#+ endpoint: <endpoint-url>`\n"
        "- **Use only ONE endpoint** per query\n"
        "- **Base your query on the provided context** (the schema and examples "
        "below) — do not invent classes, properties, or namespaces\n"
        "- **Use the exact prefixes** and class/property names from the schema "
        "documentation\n\n"
        "## Knowledge Base\n"
        "The following {docs_count} documents contain relevant query examples and "
        "class schemas to help you construct an accurate response:\n\n"
        "{formatted_docs}\n")

    try:
        mcp._tool_manager._tools.pop("search_sparql_docs", None)
    except Exception:
        pass

    EXAMPLES = "SPARQL endpoints query examples"

    @mcp.tool(description=SEARCH_SPARQL_DOCS_TOOL_DESC)
    async def search_sparql_docs(question: str, potential_classes: list[str],
                                 steps: list[str],
                                 endpoint_url: str | None = None) -> str:
        # When the endpoint is known, restrict retrieval to docs for THAT endpoint
        # so one backend's vocabulary never leaks into another's query.
        ep_cond = ([FieldCondition(key="endpoint_url",
                                   match=MatchValue(value=endpoint_url))]
                   if endpoint_url else [])
        relevant = []
        for emb in embedding_model.embed([question, *steps, *potential_classes]):
            relevant.extend(
                d for d in qdrant_client.query_points(
                    query=emb, collection_name=settings.docs_collection_name,
                    limit=settings.default_number_of_retrieved_docs,
                    query_filter=Filter(must=[FieldCondition(
                        key="doc_type", match=MatchValue(value=EXAMPLES))] + ep_cond),
                ).points
                if d.payload and d.payload.get("answer") not in {
                    e.payload.get("answer") if e.payload else None for e in relevant})
            relevant.extend(
                d for d in qdrant_client.query_points(
                    query=emb, collection_name=settings.docs_collection_name,
                    limit=settings.default_number_of_retrieved_docs,
                    query_filter=Filter(
                        must=ep_cond,
                        must_not=[FieldCondition(
                            key="doc_type", match=MatchValue(value=EXAMPLES))]),
                ).points
                if d.payload and d.payload.get("answer") not in {
                    e.payload.get("answer") if e.payload else None for e in relevant})
        return PROMPT_TOOL_SPARQL.format(docs_count=str(len(relevant)),
                                         formatted_docs=format_docs(relevant))

    return True


# ---------------------------------------------------------------------------
# Full query log: every execute_sparql_query call (incl. failures) is appended as
# one JSONL object — the analyzable record of what chatbot AND bench actually ran.
# Path $BENCH_QUERY_LOG (default /app/out/query_log.jsonl); "" disables.
# ---------------------------------------------------------------------------
_QUERY_LOG = os.getenv("BENCH_QUERY_LOG", "/app/out/query_log.jsonl")


def _register_execute_override() -> bool:
    """Wrap execute_sparql_query so every call (success OR failure) is logged as a
    JSONL line (ts, backend, endpoint, full query, ok, error, result preview). The
    original tool still runs unchanged — this only observes."""
    if not _QUERY_LOG:
        return False
    from sparql_llm.mcp_server import execute_sparql_query as _orig_execute
    import datetime as _dt

    try:
        pathlib.Path(_QUERY_LOG).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        mcp._tool_manager._tools.pop("execute_sparql_query", None)
    except Exception:
        pass

    import threading as _threading
    _log_lock = _threading.Lock()

    def _log(rec: dict) -> None:
        line = _json.dumps(rec, default=str)
        print(f"[qlog] {rec.get('ok')} {rec.get('backend')} "
              f"{len(rec.get('sparql_query',''))}c", flush=True)
        # Concurrent fq+composition tool calls race on the file: serialise with a
        # lock, open-append-flush-close per write (atomic O_APPEND), so no write is
        # lost or interleaved. os.write on a fresh fd appends atomically on POSIX.
        try:
            with _log_lock:
                fd = os.open(_QUERY_LOG,
                             os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                try:
                    os.write(fd, (line + "\n").encode("utf-8"))
                finally:
                    os.close(fd)
        except Exception as e:
            print(f"[qlog] WRITE FAILED: {type(e).__name__}: {e}", flush=True)

    @mcp.tool()
    async def execute_sparql_query(sparql_query: str, endpoint_url: str) -> str:
        """Execute a SPARQL query against the endpoint and return the results."""
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
        rec = {"ts": ts, "tool": "execute_sparql_query",
               "backend": _backend_of(endpoint_url), "endpoint_url": endpoint_url,
               "sparql_query": sparql_query}
        try:
            res = _orig_execute(sparql_query, endpoint_url)
            res = await res if hasattr(res, "__await__") else res
            text = res if isinstance(res, str) else str(res)
            # the upstream tool returns an error STRING rather than raising for
            # a bad query / endpoint error — treat those as failures too so the
            # log distinguishes "ran fine" from "ran and errored".
            low = text.lower()
            ok = not any(m in low for m in (
                "error", "malformed", "exception", "parse error",
                "failed", "bad request"))
            rec.update({"ok": ok, "result_preview": text[:2000],
                        "result_chars": len(text)})
            _log(rec)
            return text
        except Exception as e:                       # a hard failure
            rec.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
            _log(rec)
            raise

    return True


if __name__ == "__main__":
    prompts = _register_prompts()
    _register_tools()
    filtered = _register_search_override()
    print(f"[bench] search_sparql_docs endpoint-filter: "
          f"{'ON' if filtered else 'OFF'}", flush=True)
    logged = _register_execute_override()
    print(f"[bench] execute_sparql_query JSONL log: "
          f"{('ON -> ' + _QUERY_LOG) if logged else 'OFF'}", flush=True)
    print(f"[skills] {len(SKILLS)} skills: {list(SKILLS)}", flush=True)
    print(f"[skills] registered prompts: {prompts}", flush=True)
    print("[skills] registered tools: list_skills, get_skill", flush=True)
    mcp.settings.host = os.getenv("FASTMCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("FASTMCP_PORT", "8888"))
    # The MCP streamable-http transport has DNS-rebinding protection that rejects
    # any Host header not in its allow-list (it only trusts localhost/127.0.0.1 by
    # default). Inside docker compose the client reaches us by the SERVICE hostname
    # (e.g. http://bench-mcp:8888/), which would 421 "Invalid Host header". Allow
    # the configured extra hosts (FASTMCP_ALLOWED_HOSTS, comma-separated; "*" = all).
    # This is a LOCAL bench tool, so trusting the compose network is fine; the
    # default (unset) keeps the strict localhost-only behaviour for a host run.
    allowed = os.getenv("FASTMCP_ALLOWED_HOSTS", "").strip()
    if allowed:
        try:
            from mcp.server.transport_security import TransportSecuritySettings
            if allowed == "*":
                mcp.settings.transport_security = TransportSecuritySettings(
                    enable_dns_rebinding_protection=False)
                print("[bench] MCP transport security: DNS-rebinding protection OFF "
                      "(FASTMCP_ALLOWED_HOSTS=*)", flush=True)
            else:
                hosts = [h.strip() for h in allowed.split(",") if h.strip()]
                mcp.settings.transport_security = TransportSecuritySettings(
                    allowed_hosts=hosts,
                    allowed_origins=[f"http://{h}" for h in hosts]
                                    + [f"https://{h}" for h in hosts])
                print(f"[bench] MCP allowed hosts: {hosts}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[bench] WARNING: could not set MCP transport security ({e!r}); "
                  f"a non-localhost Host header may 421.", file=sys.stderr, flush=True)
    mcp.run(transport="streamable-http")

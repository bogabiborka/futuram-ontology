"""Build-time patches to the installed sparql-llm package (idempotent).

Patch 1 wraps extract_user_question's async structured-output in a retry loop;
Ollama's ainvoke returns None on a fraction of calls, and each retry is independent.
Runs at DOCKER BUILD time, so it locates the package via find_spec, never importing it
(import would download a fastembed model).
"""
import importlib.util
import pathlib


def _pkg_dir(name):
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit(f"cannot locate installed package {name!r}")
    return pathlib.Path(list(spec.submodule_search_locations)[0])


_SPARQL_LLM_DIR = _pkg_dir("sparql_llm")

TARGET = _SPARQL_LLM_DIR / "agent" / "nodes" / "llm_extraction.py"
src = TARGET.read_text()

OLD = (
    '    structured_question = StructuredQuestion.model_validate(\n'
    '        await model.ainvoke(message_value, {**config, "configurable": {"stream": False}})\n'
    '    )\n'
)

NEW = (
    '    # PATCHED: Ollama async structured-output (ainvoke) intermittently returns\n'
    '    # None; retry a few times (each attempt is independent) before giving up.\n'
    '    structured_question = None\n'
    '    _last = None\n'
    '    for _attempt in range(5):\n'
    '        _raw = await model.ainvoke(message_value, {**config, "configurable": {"stream": False}})\n'
    '        if _raw is not None:\n'
    '            try:\n'
    '                structured_question = StructuredQuestion.model_validate(_raw)\n'
    '                break\n'
    '            except Exception as _e:  # noqa: BLE001\n'
    '                _last = _e\n'
    '    if structured_question is None:\n'
    '        # last resort: minimal valid StructuredQuestion so the agent proceeds\n'
    '        # (access_resources => it will still try to query) instead of 500ing.\n'
    '        structured_question = StructuredQuestion(\n'
    '            intent="access_resources", question_steps=[],\n'
    '            extracted_classes=[], extracted_entities=[],\n'
    '        )\n'
)

if OLD in src:
    TARGET.write_text(src.replace(OLD, NEW, 1))
    print(f"PATCHED {TARGET}")
elif "PATCHED: Ollama async structured-output" in src:
    print(f"already patched: {TARGET}")
else:
    raise SystemExit(
        f"PATCH TARGET NOT FOUND in {TARGET} — the upstream code shape changed; "
        "inspect llm_extraction.py and update patch_extract_retry.py."
    )


# ---------------------------------------------------------------------------
# Patch 2: server-side endpoint host rewrite. The agent runs INSIDE the chat
# container, so localhost:4703x must become host.docker.internal:4703x; rewrite at
# query_sparql() (utils.py), the single execution chokepoint. Browser links stay.
# ---------------------------------------------------------------------------
UTILS = _SPARQL_LLM_DIR / "utils.py"     # by path — never import the module
usrc = UTILS.read_text()

U_OLD = 'def query_sparql(\n'
U_NEW = (
    'def _futuram_rewrite_endpoint_host(_url: str) -> str:\n'
    '    """localhost:4703x -> host.docker.internal:4703x for in-container execution.\n'
    '    The browser keeps the localhost URL; only the agent\'s own HTTP call is\n'
    '    redirected to the host-published Fuseki port. No-op outside Docker."""\n'
    '    import os\n'
    '    host = os.environ.get("FUTURAM_FUSEKI_HOST", "host.docker.internal")\n'
    '    # plain token swap (no regex) so it works for any localhost:PORT form\n'
    '    return _url.replace("//localhost:", "//" + host + ":").replace("//localhost/", "//" + host + "/")\n'
    '\n\n'
    'def query_sparql(\n'
)

if U_OLD in usrc and '_futuram_rewrite_endpoint_host' not in usrc:
    usrc = usrc.replace(U_OLD, U_NEW, 1)
    # Apply the rewrite at the top of the body (right after the docstring line).
    DOC = '"""Execute a SPARQL query on a SPARQL endpoint or its service description using httpx or a RDF turtle file using rdflib."""\n'
    BODY_OLD = DOC + '    query_resp: dict[str, Any] = {"results": {"bindings": []}}\n'
    BODY_NEW = (
        DOC
        + '    endpoint_url = _futuram_rewrite_endpoint_host(endpoint_url)\n'
        + '    query_resp: dict[str, Any] = {"results": {"bindings": []}}\n'
    )
    if BODY_OLD not in usrc:
        raise SystemExit(
            f"query_sparql body shape changed in {UTILS}; update patch_extract_retry.py."
        )
    usrc = usrc.replace(BODY_OLD, BODY_NEW, 1)
    UTILS.write_text(usrc)
    print(f"PATCHED endpoint host rewrite in {UTILS}")
elif '_futuram_rewrite_endpoint_host' in usrc:
    print(f"already patched (host rewrite): {UTILS}")
else:
    raise SystemExit(
        f"query_sparql not found in {UTILS} — upstream changed; update the patch."
    )


# ---------------------------------------------------------------------------
# Patch 3: rewrite the "Run/edit query" button from the sib-swiss hosted
# sparql-editor to the Jena Fuseki UI, deriving the Fuseki UI URL from the
# endpoint itself (Query.vue reads $route.query.query and prefills YASQE).
# ---------------------------------------------------------------------------
import re  # noqa: E402

_ASSETS = _SPARQL_LLM_DIR / "agent" / "webapp" / "assets"
_LINK_RE = re.compile(
    r'`https://sib-swiss\.github\.io/sparql-editor/\?'
    r'\$\{(\w+)\?`endpoint=\$\{\1\}&`:""\}'
    r'query=\$\{encodeURIComponent\((\w+)\)\}`'
)


def _jena_link_builder(m: "re.Match") -> str:
    e, q = m.group(1), m.group(2)
    # endpoint http(s)://HOST/<dataset>/sparql -> HOST/#/dataset/<dataset>/query
    return (
        '`${' + e + '?' + e
        + '.replace(/\\/sparql\\/?$/,"")'
        + '.replace(/^(https?:\\/\\/[^\\/]+)\\/(.*)$/,"$1/#/dataset/$2/query")'
        + ':"/"}?query=${encodeURIComponent(' + q + ')}`'
    )


_patched_any = False
for _js in sorted(_ASSETS.glob("*.js")):
    _jsrc = _js.read_text()
    _new, _n = _LINK_RE.subn(_jena_link_builder, _jsrc)
    if _n:
        _js.write_text(_new)
        print(f"PATCHED editor link ({_n}x) in {_js}")
        _patched_any = True
    elif "/#/dataset/" in _jsrc:
        print(f"already patched (editor link): {_js}")
        _patched_any = True

if not _patched_any:
    raise SystemExit(
        f"editor-link builder not found in {_ASSETS}/*.js — the upstream webapp "
        "bundle changed; inspect it and update patch_extract_retry.py."
    )


# ---------------------------------------------------------------------------
# Patch 4: two adjustments to mcp_server.cli() so --http starts cleanly:
#  (a) run only the streamable-http server (drop the preceding bare mcp.run() stdio);
#  (b) pass uvicorn log_config=None so it does not apply its dictConfig formatter.
# ---------------------------------------------------------------------------
MCP_SRV = _SPARQL_LLM_DIR / "mcp_server.py"
msrc = MCP_SRV.read_text()
M_OLD = (
    '    if args.http:\n'
    '        mcp.run()\n'
    '        mcp.settings.port = args.port\n'
    '        mcp.settings.log_level = "INFO"\n'
    '        mcp.run(transport="streamable-http")\n'
)
M_NEW = (
    '    if args.http:\n'
    '        # PATCHED: run only the streamable-http server (no preceding stdio run).\n'
    '        mcp.settings.port = args.port\n'
    '        mcp.settings.log_level = "INFO"\n'
    '        mcp.run(transport="streamable-http")\n'
)
if M_OLD in msrc:
    MCP_SRV.write_text(msrc.replace(M_OLD, M_NEW, 1))
    print(f"PATCHED mcp cli pre-run in {MCP_SRV}")
elif "PATCHED: run only the streamable-http server" in msrc:
    print(f"already patched (mcp cli): {MCP_SRV}")
else:
    print(f"NOTE: mcp cli shape changed in {MCP_SRV}; pre-run patch skipped")

# (b) uvicorn log_config=None in FastMCP's streamable-http (and SSE) runners.
_MCP_DIR = _pkg_dir("mcp")
FASTMCP_SRV = _MCP_DIR / "server" / "fastmcp" / "server.py"
fsrc = FASTMCP_SRV.read_text()
F_OLD = (
    '        config = uvicorn.Config(\n'
    '            starlette_app,\n'
    '            host=self.settings.host,\n'
    '            port=self.settings.port,\n'
    '            log_level=self.settings.log_level.lower(),\n'
    '        )\n'
)
F_NEW = (
    '        config = uvicorn.Config(\n'
    '            starlette_app,\n'
    '            host=self.settings.host,\n'
    '            port=self.settings.port,\n'
    '            log_level=self.settings.log_level.lower(),\n'
    '            log_config=None,  # PATCHED: do not apply uvicorn dictConfig\n'
    '        )\n'
)
_fn = fsrc.count(F_OLD)
if _fn:
    FASTMCP_SRV.write_text(fsrc.replace(F_OLD, F_NEW))   # both SSE + streamable
    print(f"PATCHED uvicorn log_config ({_fn}x) in {FASTMCP_SRV}")
elif "PATCHED: do not apply uvicorn dictConfig" in fsrc:
    print(f"already patched (uvicorn log_config): {FASTMCP_SRV}")
else:
    raise SystemExit(
        f"uvicorn.Config block not found in {FASTMCP_SRV} — the mcp package "
        "shape changed; inspect it and update patch_extract_retry.py."
    )


# ---------------------------------------------------------------------------
# Patch 5: make the chat intro page DYNAMIC about its backends — inject the real
# backend list + active one from settings.endpoints into the template context
# (id = endpoint_url's :PORT; active = settings.default_backend else first).
# ---------------------------------------------------------------------------
MAIN = _SPARQL_LLM_DIR / "agent" / "main.py"
asrc = MAIN.read_text()
A_OLD = (
    '            "chat_endpoint": "/chat",\n'
    '            "feedback_endpoint": "/feedback",\n'
    '            "examples": ",".join(settings.example_questions),\n'
    '        },\n'
    '    )\n'
)
A_NEW = (
    '            "chat_endpoint": "/chat",\n'
    '            "feedback_endpoint": "/feedback",\n'
    '            "examples": ",".join(settings.example_questions),\n'
    '            # PATCHED: real backend list + active backend, so the intro page\n'
    '            # renders the truth from data and a picker can offer the choices.\n'
    '            "backends": _futuram_backends(),\n'
    '            "active_backend": _futuram_active_backend(),\n'
    '            "app_name": settings.app_name,\n'
    '        },\n'
    '    )\n'
)
# helper functions injected just above the route
A_HELPERS = (
    'def _futuram_backend_id(ep: dict) -> str:\n'
    '    """Stable id for a backend = the host:port of its endpoint_url."""\n'
    '    from urllib.parse import urlparse\n'
    '    u = urlparse(ep.get("endpoint_url", ""))\n'
    '    return (u.netloc or ep.get("label") or "backend").replace(":", "_")\n'
    '\n\n'
    'def _futuram_backend_reachable(endpoint_url: str) -> bool:\n'
    '    """Quick liveness probe (ASK{}) so the picker only offers backends whose\n'
    '    Fuseki is actually up. Uses the same localhost->host.docker.internal\n'
    '    rewrite as query execution. Short timeout; failures => not reachable."""\n'
    '    if not endpoint_url:\n'
    '        return False\n'
    '    try:\n'
    '        import httpx\n'
    '        from sparql_llm.utils import _futuram_rewrite_endpoint_host as _rw\n'
    '        url = _rw(endpoint_url)\n'
    '        r = httpx.get(url, params={"query": "ASK{}"},\n'
    '                      headers={"Accept": "application/sparql-results+json"},\n'
    '                      timeout=2.0)\n'
    '        return r.status_code == 200\n'
    '    except Exception:\n'
    '        return False\n'
    '\n\n'
    'def _futuram_backends() -> list:\n'
    '    """The configured backends as plain dicts for the template / picker, each\n'
    '    with a live `reachable` flag (the picker disables the dead ones)."""\n'
    '    out = []\n'
    '    for ep in settings.endpoints:\n'
    '        url = ep.get("endpoint_url", "")\n'
    '        out.append({\n'
    '            "id": _futuram_backend_id(ep),\n'
    '            "label": ep.get("label") or url,\n'
    '            "description": ep.get("description", ""),\n'
    '            "endpoint_url": url,\n'
    '            "homepage_url": ep.get("homepage_url", ""),\n'
    '            "reachable": _futuram_backend_reachable(url),\n'
    '        })\n'
    '    return out\n'
    '\n\n'
    'def _futuram_active_backend() -> str:\n'
    '    """The active backend id: settings.default_backend if set AND reachable,\n'
    '    else the first reachable backend, else the first configured."""\n'
    '    bks = _futuram_backends()\n'
    '    reachable = [b for b in bks if b["reachable"]]\n'
    '    want = getattr(settings, "default_backend", None)\n'
    '    for b in reachable:\n'
    '        if want and (b["id"] == want or b["endpoint_url"] == want\n'
    '                     or b["label"] == want):\n'
    '            return b["id"]\n'
    '    if reachable:\n'
    '        return reachable[0]["id"]\n'
    '    return bks[0]["id"] if bks else ""\n'
    '\n\n'
    '@app.get("/", response_class=HTMLResponse, include_in_schema=False)\n'
)
ROUTE_ANCHOR = '@app.get("/", response_class=HTMLResponse, include_in_schema=False)\n'
if A_OLD in asrc and "_futuram_backends" not in asrc:
    asrc = asrc.replace(A_OLD, A_NEW, 1)
    asrc = asrc.replace(ROUTE_ANCHOR, A_HELPERS, 1)   # inject helpers above route
    MAIN.write_text(asrc)
    print(f"PATCHED dynamic backend context in {MAIN}")
elif "_futuram_backends" in asrc:
    print(f"already patched (dynamic backends): {MAIN}")
else:
    raise SystemExit(
        f"GET / template context not found in {MAIN} — the agent main.py shape "
        "changed; inspect it and update patch_extract_retry.py."
    )


# ---------------------------------------------------------------------------
# Patch 6: SCOPE the chat to the picked backend (header X-FutuRaM-Backend) via
# RunnableConfig `backend_endpoint`, so retrieval FILTERS Qdrant to that endpoint:
# (a) Configuration field, (b) /chat resolves the header, (c) retrieval_docs cond.
# ---------------------------------------------------------------------------

# (a) Configuration field --------------------------------------------------
CFG = _SPARQL_LLM_DIR / "config.py"
csrc = CFG.read_text()
C_OLD = (
    '    max_try_fix_sparql: int = field(\n'
    '        default=settings.default_max_try_fix_sparql,\n'
    '        metadata={"description": "The maximum number of tries when calling the model to fix a SPARQL query."},\n'
    '    )\n'
)
C_NEW = C_OLD + (
    '\n'
    '    # PATCHED: the backend the user picked (its endpoint_url). Empty = no\n'
    '    # scoping (all backends). Set per-request from the X-FutuRaM-Backend header.\n'
    '    backend_endpoint: str = field(\n'
    '        default="",\n'
    '        metadata={"description": "FutuRaM: scope retrieval/queries to this single backend endpoint_url."},\n'
    '    )\n'
)
if C_OLD in csrc and "backend_endpoint" not in csrc:
    CFG.write_text(csrc.replace(C_OLD, C_NEW, 1))
    print(f"PATCHED Configuration.backend_endpoint in {CFG}")
elif "backend_endpoint" in csrc:
    print(f"already patched (backend_endpoint): {CFG}")
else:
    raise SystemExit(f"Configuration max_try_fix_sparql field not found in {CFG}.")

# (b) /chat reads the header and sets configurable -------------------------
asrc = MAIN.read_text()
H_OLD = (
    '    config = RunnableConfig(\n'
    '        configurable={\n'
    '            "model": chat_request.model,\n'
    '            "validate_output": chat_request.validate_output,\n'
    '            "enable_sparql_execution": chat_request.enable_sparql_execution,\n'
    '        },\n'
)
H_NEW = (
    '    # PATCHED: resolve the picked backend (X-FutuRaM-Backend) to its endpoint.\n'
    '    _picked = request.headers.get("X-FutuRaM-Backend", "")\n'
    '    _backend_endpoint = _futuram_endpoint_for(_picked)\n'
    '    config = RunnableConfig(\n'
    '        configurable={\n'
    '            "model": chat_request.model,\n'
    '            "validate_output": chat_request.validate_output,\n'
    '            "enable_sparql_execution": chat_request.enable_sparql_execution,\n'
    '            "backend_endpoint": _backend_endpoint,\n'
    '        },\n'
)
RESOLVER_FN = (
    'def _futuram_endpoint_for(backend_id: str) -> str:\n'
    '    """Map a picked backend id (or endpoint_url/label) to its endpoint_url.\n'
    '    Empty / unknown -> "" (no scoping)."""\n'
    '    if not backend_id:\n'
    '        return ""\n'
    '    for b in _futuram_backends():\n'
    '        if backend_id in (b["id"], b["endpoint_url"], b["label"]):\n'
    '            return b["endpoint_url"]\n'
    '    return ""\n'
    '\n\n'
    'def _futuram_backend_id(ep: dict) -> str:\n'   # re-anchor: insert before existing helper
)
if H_OLD in asrc and "_futuram_endpoint_for" not in asrc:
    asrc = asrc.replace(H_OLD, H_NEW, 1)
    asrc = asrc.replace('def _futuram_backend_id(ep: dict) -> str:\n', RESOLVER_FN, 1)
    MAIN.write_text(asrc)
    print(f"PATCHED /chat backend scoping in {MAIN}")
elif "_futuram_endpoint_for" in asrc:
    print(f"already patched (/chat backend scoping): {MAIN}")
else:
    raise SystemExit(f"/chat RunnableConfig block not found in {MAIN}.")

# (c) retrieval filters scope to backend_endpoint --------------------------
# Only retrieval_docs.py matters (it feeds the LLM the schema + examples);
# retrieval_entities.py is gated off by default and already post-filters.
RET = _SPARQL_LLM_DIR / "agent" / "nodes" / "retrieval_docs.py"
rsrc = RET.read_text()
if "_futuram_backend_cond" in rsrc:
    print(f"already patched (retrieval scope): {RET}")
else:
    helper = (
        "\n# PATCHED (FutuRaM): scope retrieval to the picked backend's endpoint.\n"
        "def _futuram_backend_cond(config):\n"
        "    try:\n"
        "        from sparql_llm.config import Configuration\n"
        "        from qdrant_client.models import FieldCondition, MatchValue\n"
        "        ep = getattr(Configuration.from_runnable_config(config), 'backend_endpoint', '')\n"
        "        return [FieldCondition(key='endpoint_url', match=MatchValue(value=ep))] if ep else []\n"
        "    except Exception:\n"
        "        return []\n"
    )
    anchor = "async def retrieve"
    if anchor not in rsrc:
        raise SystemExit(f"retrieve fn not found in {RET}.")
    rsrc = rsrc.replace(anchor, helper + "\n\n" + anchor, 1)
    n1 = rsrc.count("query_filter=search_filter,")
    rsrc = rsrc.replace("query_filter=search_filter,",
                        "query_filter=Filter(must=(search_filter.must or []) + _futuram_backend_cond(config)),")
    n2 = rsrc.count("query_filter=Filter(\n                        must=[")
    rsrc = rsrc.replace("query_filter=Filter(\n                        must=[",
                        "query_filter=Filter(\n                        must=_futuram_backend_cond(config) + [")
    if n1 + n2 == 0:
        raise SystemExit(
            f"no query_filter sites matched in {RET} — the retrieval node shape "
            "changed; inspect it and update patch_extract_retry.py.")
    RET.write_text(rsrc)
    print(f"PATCHED retrieval backend scope in {RET} ({n1} default + {n2} examples filters)")

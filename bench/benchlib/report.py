"""Configuration constants, model / live-dir resolution, the runtime-environment
probe, and the human-readable run summary. No LLM-facing strings live here."""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

# Endpoint URLs the model is told to query, per backend id. These must match the
# endpoint_url values in bench/settings.bench.json (the MCP server indexes docs
# keyed by exactly these URLs).
DEFAULT_ENDPOINTS = {
    "fq": "http://localhost:47040/query/sparql",
    "composition": "http://localhost:47040/composition/sparql",
}
DEFAULT_MCP_URL = "http://localhost:47898/"

# ONE canonical live-transcript dir, shared with bench/observer/lib/runs.js, so a
# run ALWAYS streams to the observer with no flag (avoids the writes-A/reads-B
# trap). Resolution: --live-dir > $BENCH_LIVE_DIR > DEFAULT_LIVE_DIR.
DEFAULT_LIVE_DIR = "bench/live"


def resolve_live_dir(cli_live_dir=None):
    """The live-transcript dir, by the shared precedence (kept identical to the
    observer's liveDir() in bench/observer/lib/runs.js)."""
    return cli_live_dir or os.getenv("BENCH_LIVE_DIR") or DEFAULT_LIVE_DIR


# --------------------------------------------------------------------------- #
# Model resolution
# --------------------------------------------------------------------------- #
def _model_from_env_file() -> str | None:
    """DEFAULT_LLM_MODEL from sparql-llm/.env (the model the deployed chat uses)."""
    env = REPO / "sparql-llm" / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith("DEFAULT_LLM_MODEL"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def resolve_model(cli_model: str | None) -> tuple[str, str]:
    """Pick the Ollama model; returns (model_tag, source) so the run prints WHERE
    it came from. Precedence (first set wins): --model > $BENCH_MODEL >
    DEFAULT_LLM_MODEL in sparql-llm/.env (matches the deployed chat)."""
    if cli_model:
        return cli_model, "--model"
    env = os.getenv("BENCH_MODEL")
    if env:
        return env, "$BENCH_MODEL"
    f = _model_from_env_file()
    if f:
        return f, "sparql-llm/.env"
    raise SystemExit(
        "No model set for the Ollama fallback. Either select a provider "
        "(--provider <name>, e.g. copilot — used by default when a token is "
        "available), or set an Ollama model:  --model <tag>  |  BENCH_MODEL=<tag>  |  "
        "DEFAULT_LLM_MODEL in sparql-llm/.env.  "
        "List models with:  uv run bench/run_bench.py --list-models")


def ollama_model_tag(model: str) -> str:
    """Ollama's python SDK wants the bare tag; strip a 'ollama/' provider prefix
    (sparql-llm stores it provider-qualified for langchain)."""
    return model.split("/", 1)[1] if model.startswith("ollama/") else model


# --------------------------------------------------------------------------- #
# Provider profiles (BYOK / BYOM)
# --------------------------------------------------------------------------- #
PROVIDERS_FILE = Path(__file__).resolve().parent.parent / "providers.json"

# Inline-tag prefixes that name a provider directly, e.g. --model anthropic/gpt..
_INLINE_PROVIDER_PREFIXES = {
    "ollama", "anthropic", "openai", "google", "gemini", "copilot",
}


def load_providers() -> dict:
    """The provider profiles (bench/providers.json). Missing file → just the
    built-in ollama default, so the bench still runs with no config."""
    try:
        raw = json.loads(PROVIDERS_FILE.read_text())
    except Exception:  # noqa: BLE001
        raw = {}
    # drop comment/metadata keys
    profiles = {k: v for k, v in raw.items()
                if isinstance(v, dict) and not k.startswith("_")}
    profiles.setdefault("ollama", {"provider": "ollama", "host_env": "OLLAMA_HOST"})
    return profiles


def _copilot_token_available(profiles: dict) -> bool:
    """True if a GitHub Copilot token can be obtained WITHOUT prompting — an env
    var (COPILOT_API_KEY / GITHUB_TOKEN / GH_TOKEN) or a cached OAuth login. Used to
    DEFAULT to Copilot when it's available, falling back to Ollama otherwise. Never
    raises (a probe), never logs the token."""
    prof = profiles.get("copilot", {})
    if os.getenv(prof.get("key_env", "COPILOT_API_KEY")):
        return True
    if os.getenv(prof.get("github_token_env", "GITHUB_TOKEN")) or os.getenv("GH_TOKEN"):
        return True
    try:
        from .oauth import load_cached_token
        return bool(load_cached_token("copilot"))
    except Exception:
        return False


def copilot_quota_snapshot(profiles: dict) -> dict | None:
    """Resolve the Copilot token (same path as a run) and fetch the account's
    live quota snapshot, so the observer can show the user their budget. Returns
    None (never raises) if no token / the fetch fails — the UI just hides the chip."""
    prof = profiles.get("copilot", {"provider": "copilot"})
    try:
        api_key, _ = _resolve_key(prof, "copilot")
    except SystemExit:
        return None
    from .llm import copilot_quota
    return copilot_quota(api_key)


def _resolve_key(profile: dict, provider: str):
    """Return (api_key, base_url) for an API provider, reading ONLY env vars named
    by the profile (BYOK). For copilot, fall back to a cached OAuth token. Raises
    a clear SystemExit naming what to set if no key is found."""
    base_url = None
    if profile.get("base_url"):
        base_url = profile["base_url"]
    elif profile.get("base_url_env"):
        base_url = os.getenv(profile["base_url_env"]) or None

    if provider == "ollama":
        return None, base_url

    key_env = profile.get("key_env")
    api_key = os.getenv(key_env) if key_env else None

    if not api_key and provider == "copilot":
        # try the explicit GitHub-token env var, then a cached OAuth login
        gh_env = profile.get("github_token_env", "GITHUB_TOKEN")
        api_key = (os.getenv(gh_env) or os.getenv("GH_TOKEN"))
        if not api_key:
            from .oauth import load_cached_token
            api_key = load_cached_token("copilot")
        if not api_key:
            raise SystemExit(
                "GitHub Copilot needs a token. Either set COPILOT_API_KEY / "
                "GITHUB_TOKEN, or run a one-time login:\n"
                "    uv run bench/run_bench.py --login copilot")

    if not api_key:
        raise SystemExit(
            f"No API key for provider {provider!r}. Set the {key_env or '<key>'} "
            f"environment variable (it is read at launch and never stored).")
    return api_key, base_url


def resolve_llm(cli_model: str | None, cli_provider: str | None,
                ollama_host: str | None = None):
    """Build the LLM client for this run. Returns (client, display, provider).

    Selection precedence:
      1. --provider <profile>        (a name in providers.json)
      2. --model provider/model      (inline provider prefix)
      3. $BENCH_PROVIDER             (a profile name)
      4. fall back to Ollama via resolve_model() (--model > $BENCH_MODEL > .env)
    A bare model tag with no provider stays Ollama (the default)."""
    from .llm import build_client
    profiles = load_providers()

    provider = None
    profile: dict = {}
    model = cli_model

    name = cli_provider or os.getenv("BENCH_PROVIDER")
    # inline provider/model tag (only when the prefix is a known provider, so a
    # real ollama tag like "ollama/gemma4:31b-cloud" still works)
    if not name and cli_model and "/" in cli_model:
        head = cli_model.split("/", 1)[0].lower()
        if head in _INLINE_PROVIDER_PREFIXES:
            name = head
            model = cli_model.split("/", 1)[1]

    if name and name != "ollama":
        profile = profiles.get(name)
        if profile is None:
            # an inline provider id without a matching profile (e.g. "openai/…")
            if name in _INLINE_PROVIDER_PREFIXES:
                profile = {"provider": name}
            else:
                raise SystemExit(
                    f"Unknown provider/profile {name!r}. Known: "
                    f"{', '.join(sorted(profiles))}. (See bench/providers.json.)")
        provider = (profile.get("provider") or name).lower()
        model = model or profile.get("model")
        if not model:
            raise SystemExit(
                f"Profile {name!r} has no model — pass --model <tag> or add a "
                f"`model` to its providers.json entry.")
        api_key, base_url = _resolve_key(profile, provider)
        client = build_client(provider, model, host=ollama_host,
                              api_key=api_key, base_url=base_url)
        disp = f"{provider}:{model}"
        return client, disp, provider

    # DEFAULT provider — prefer GitHub Copilot when a token is available (no prompt
    # needed), else fall back to Ollama. An explicit --provider/$BENCH_PROVIDER/inline
    # tag above wins; this only decides the no-provider default. Set
    # BENCH_DEFAULT_PROVIDER=ollama to force the old Ollama default.
    # Only auto-default to Copilot when NO model was explicitly requested — an
    # explicit --model/$BENCH_MODEL is an Ollama tag (the bench's documented default
    # model) and must not be sent to Copilot, which doesn't have it. Use the copilot
    # profile's own default model for the auto path.
    default_pref = (os.getenv("BENCH_DEFAULT_PROVIDER") or "").lower()
    if (default_pref != "ollama" and model is None
            and _copilot_token_available(profiles)):
        prof = profiles.get("copilot", {"provider": "copilot"})
        cop_model = prof.get("model")
        if cop_model:
            try:
                api_key, base_url = _resolve_key(prof, "copilot")
                client = build_client("copilot", cop_model, host=ollama_host,
                                      api_key=api_key, base_url=base_url)
                return client, f"copilot:{cop_model} (default — token available)", "copilot"
            except SystemExit:
                pass        # token probe lied / key invalid -> fall back to Ollama

    # fall back — Ollama (--model > $BENCH_MODEL > sparql-llm/.env). If NOTHING is
    # configured (no key/token for any cloud provider AND no Ollama model/daemon),
    # fail with ONE clear, non-technical message instead of a deep stack trace.
    try:
        raw_model, src = resolve_model(model)
    except SystemExit:
        raise SystemExit(_no_llm_message(profiles, ollama_host))
    tag = ollama_model_tag(raw_model)
    _preflight_ollama(tag, ollama_host, profiles)
    client = build_client("ollama", tag, host=ollama_host)
    return client, f"ollama:{tag} (from {src})", "ollama"


# --------------------------------------------------------------------------- #
# Friendly "no LLM configured" + Ollama preflight (clear errors for non-experts)
# --------------------------------------------------------------------------- #
def _configured_cloud_providers(profiles: dict) -> list[str]:
    """Provider profiles whose key/token IS present right now (usable today)."""
    usable = []
    for name, prof in profiles.items():
        provider = (prof.get("provider") or name).lower()
        if provider == "ollama":
            continue
        try:
            _resolve_key(prof, provider)
            usable.append(name)
        except SystemExit:
            pass
    return usable


def _no_llm_message(profiles: dict, ollama_host: str | None) -> str:
    host = ollama_host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
    return (
        "\n" + "=" * 70 + "\n"
        "  No language model is set up yet — the bench has nothing to run.\n"
        + "=" * 70 + "\n\n"
        "Pick ONE of these (each is a few minutes):\n\n"
        "  A) Use a cloud model you already pay for (BYOK = bring your own key):\n"
        "       • GitHub Copilot — open the web UI and click \"Sign in with\n"
        "         GitHub\" (no key to copy). Then pick 'copilot' as the provider.\n"
        "       • OpenAI / Anthropic / Gemini / Groq / OpenRouter — set the\n"
        "         matching environment variable before starting, e.g.\n"
        "           ANTHROPIC_API_KEY=sk-...   (or OPENAI_API_KEY, GOOGLE_API_KEY,\n"
        "           GROQ_API_KEY, OPENROUTER_API_KEY)\n"
        "         then choose that provider in the UI.\n\n"
        "  B) Run a model locally for free (BYOM = bring your own model):\n"
        "       1. Install Ollama from https://ollama.com  and start it.\n"
        "       2. Download a model, e.g.:   ollama pull llama3.1:8b\n"
        f"       3. Make sure it is reachable at {host}\n"
        "       (the bench talks to your host's Ollama, not one in Docker).\n\n"
        "Then start the bench again. Full guide: bench/README.md → "
        "\"Bring your own key / model\".\n")


def _preflight_ollama(tag: str, ollama_host: str | None, profiles: dict) -> None:
    """Before an Ollama run, verify the daemon is up AND the model is present.
    Raise a clear, non-technical SystemExit if not (else the run dies mid-loop
    with an opaque connection error)."""
    host = (ollama_host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
    try:
        from ollama import Client
        installed = [m.get("model") or m.get("name")
                     for m in Client(host=host).list().get("models", [])]
    except Exception:  # noqa: BLE001 — daemon unreachable
        cloud = _configured_cloud_providers(profiles)
        hint = (f"\n\nTip: you DO have a cloud provider ready ({', '.join(cloud)}) — "
                f"select it in the UI or pass --provider <name> instead of using Ollama."
                if cloud else "")
        raise SystemExit(
            f"\nCannot reach Ollama at {host}.\n"
            f"The bench is set to use the local model '{tag}', but no Ollama daemon\n"
            f"is answering there.\n\n"
            f"  • Install + start Ollama:  https://ollama.com  (then `ollama serve`)\n"
            f"  • From inside Docker the host daemon is http://host.docker.internal:11434\n"
            f"  • Or use a cloud provider instead (see bench/README.md → BYOK/BYOM)."
            + hint)
    if installed and tag not in installed:
        avail = ", ".join(sorted(installed)[:8]) or "(none installed)"
        raise SystemExit(
            f"\nThe model '{tag}' is not installed in your Ollama.\n"
            f"  • Download it:  ollama pull {tag}\n"
            f"  • Or choose one you already have: {avail}\n"
            f"  • Or use a cloud provider (see bench/README.md → BYOK/BYOM).")


def provider_models(name: str, ollama_host: str | None = None) -> list[str]:
    """The model ids a provider/profile offers, for the UI dropdown. Builds the
    client (reusing the same key/token resolution as a run — incl. the cached
    Copilot OAuth token) and asks it. Returns [] if it can't authenticate/list."""
    from .llm import build_client
    profiles = load_providers()
    name = (name or "ollama").lower()
    if name == "ollama":
        return build_client("ollama", "x", host=ollama_host).list_models()
    profile = profiles.get(name) or ({"provider": name} if name in _INLINE_PROVIDER_PREFIXES else None)
    if profile is None:
        return []
    provider = (profile.get("provider") or name).lower()
    # a placeholder model is fine — list_models() doesn't use it, only construction
    model = profile.get("model") or "x"
    try:
        api_key, base_url = _resolve_key(profile, provider)
        client = build_client(provider, model, host=ollama_host,
                              api_key=api_key, base_url=base_url)
        return client.list_models()
    except SystemExit:
        return []   # no key/token configured -> empty (UI shows "connect first")
    except Exception:  # noqa: BLE001
        return []


def list_models(ollama_host: str) -> int:
    """Print the model tags installed on the Ollama daemon, and which one the
    benchmark would pick by default — so it is trivial to choose one."""
    try:
        from ollama import Client
        models = Client(host=ollama_host).list().get("models", [])
        tags = sorted(m.get("model") or m.get("name") for m in models)
        print(f"# Ollama models on {ollama_host}:")
        for t in tags:
            print(f"  {t}")
        try:
            cur, src = resolve_model(None)
            print(f"\n# default pick: {ollama_model_tag(cur)}  (from {src})")
        except SystemExit:
            print("\n# no default model configured — set --model / $BENCH_MODEL / "
                  "DEFAULT_LLM_MODEL")
        print("# choose one with:  --model <tag>   or   BENCH_MODEL=<tag> uv run …")
    except Exception as e:  # noqa: BLE001 — daemon down is fine; still list profiles
        print(f"# could not reach Ollama at {ollama_host}: {e}")
        print("# (Ollama is the default; BYOK/BYOM providers below need no daemon.)")
    # also show the BYOK/BYOM provider profiles
    profiles = load_providers()
    print(f"\n# provider profiles (bench/providers.json) — use --provider <name>:")
    for name, p in profiles.items():
        prov = p.get("provider", name)
        mdl = p.get("model", "(set with --model)")
        key = p.get("key_env")
        keynote = f"  key:${key}" if key else ""
        print(f"  {name:12s} provider={prov:10s} model={mdl}{keynote}")
    return 0


# --------------------------------------------------------------------------- #
# Runtime environment probe
# --------------------------------------------------------------------------- #
def environment(model: str, ollama_host: str, endpoints: dict,
                client=None) -> dict:
    """Experimental-setup record (SI-3 style), DETECTED AT RUNTIME: hardware,
    software (Python/triplestore/Java), LLM runtime. Every probe fails soft to None;
    non-Ollama providers record the client's non-secret descriptor (never a key)."""
    import platform
    import shutil
    import subprocess
    import urllib.request

    def _run(cmd):
        try:
            exe = shutil.which(cmd[0])
            if not exe:
                return None
            out = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True,
                                 timeout=8)
            return (out.stdout or out.stderr).strip() or None
        except Exception:
            return None

    def _get_json(url, data=None):
        try:
            req = urllib.request.Request(
                url, data=(json.dumps(data).encode() if data else None),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read())
        except Exception:
            return None

    # ---- hardware (live) ----
    cpu = None
    if platform.system() == "Darwin":
        cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    elif platform.system() == "Linux":
        cpu = _run(["sh", "-c", "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2"])
    cpu = cpu or platform.processor() or None
    ram_bytes = None
    try:
        if platform.system() == "Darwin":
            v = _run(["sysctl", "-n", "hw.memsize"])
            ram_bytes = int(v) if v and v.isdigit() else None
        else:
            import os as _os
            ram_bytes = _os.sysconf("SC_PAGE_SIZE") * _os.sysconf("SC_PHYS_PAGES")
    except Exception:
        ram_bytes = None

    # ---- LLM runtime (live) ----
    provider = getattr(client, "provider", "ollama")
    ver = model_details = None
    if provider == "ollama":
        ver = _get_json(ollama_host.rstrip("/") + "/api/version")
        show = _get_json(ollama_host.rstrip("/") + "/api/show",
                         {"model": ollama_model_tag(model)})
        model_details = (show or {}).get("details") if show else None

    # ---- triplestore (live, from the served endpoints) ----
    triplestore = None
    for url in endpoints.values():
        base = url.split("/sparql")[0].rsplit("/", 1)[0]
        info = _get_json(base + "/$/server")          # Fuseki admin ping
        if info:
            triplestore = {"name": "Apache Jena Fuseki",
                           "version": info.get("version")}
            break

    return {
        "hardware": {
            "cpu": cpu,
            "ram_bytes": ram_bytes,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "software": {
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "java": _run(["java", "-version"]),
            "triplestore": triplestore,
        },
        "llm": {
            "provider": provider,
            "model": model,
            "ollama_host": ollama_host if provider == "ollama" else None,
            "ollama_version": (ver or {}).get("version"),
            "model_details": model_details,
            # non-secret provider descriptor (base_url/host, never a key)
            "runtime": client.describe() if client is not None else None,
        },
    }


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
def print_summary(results: list[dict], backends: list[str]) -> None:
    print("=" * 64)
    print("SUMMARY — correctness and speed per backend")
    print("=" * 64)
    for be in backends:
        rs = [r for r in results if r["backend"] == be]
        if not rs:
            continue
        n = len(rs)
        ok = sum(1 for r in rs if r["correct"])
        times = [r["seconds"] for r in rs]
        sparql = [r["sparql_runs"] for r in rs]
        attempts = [r.get("attempts", 1) for r in rs]
        timeouts = sum(1 for r in rs if r.get("timed_out"))
        tok_tot = sum(r.get("tokens_total", 0) for r in rs)
        tok_in = sum(r.get("tokens_in", 0) for r in rs)
        tok_out = sum(r.get("tokens_out", 0) for r in rs)
        avg = sum(times) / n if n else 0
        med = sorted(times)[n // 2] if n else 0
        # per-tool total time across all this backend's runs
        tool_tot = {}
        for r in rs:
            for t, v in r.get("tool_seconds_by_type", {}).items():
                tool_tot[t] = round(tool_tot.get(t, 0.0) + v["seconds"], 1)
        tt = "  ".join(f"{t}={s}s" for t, s in sorted(tool_tot.items()))
        print(f"  {be:12s}  correct {ok}/{n} ({100*ok/n:.0f}%)   "
              f"avg {avg:.1f}s  median {med:.1f}s  "
              f"avg SPARQL {sum(sparql)/n:.1f}  avg attempts {sum(attempts)/n:.1f}"
              f"  timeouts {timeouts}")
        print(f"                tokens: total {tok_tot:,}  "
              f"avg/q {tok_tot//n if n else 0:,}  "
              f"(in {tok_in:,} / out {tok_out:,})")
        if tt:
            print(f"                tool time totals: {tt}")
        # FAILURE BREAKDOWN — what KIND of error the fails were (triaged category),
        # so the run shows whether they were wrong-class (resolution), wrong-value
        # (arithmetic), wrong-uncertainty, no-answer, etc. — not just a fail count.
        from collections import Counter
        cats = Counter(r.get("error_category") for r in rs
                       if not r["correct"] and r.get("error_category"))
        if cats:
            brk = "  ".join(f"{c}×{k}" for c, k in cats.most_common())
            print(f"                fails by kind: {brk}")
        # LOAD KPIs — the "required load" of this approach (averaged over cases)
        ks = [r.get("kpis") or {} for r in rs]
        ks = [k for k in ks if k]
        if ks:
            def _avg(key): return sum(k.get(key, 0) for k in ks) / len(ks)
            def _sum(key): return sum(k.get(key, 0) for k in ks)
            print(f"                LOAD: avg wrong-query ratio {_avg('wrong_query_ratio'):.2f} "
                  f"({_sum('wrong_queries')} wrong / {_sum('queries_to_answer')} q total)  "
                  f"query-time {_sum('sparql_seconds'):.1f}s "
                  f"(avg/q {(_sum('sparql_seconds')/max(1,_sum('queries_to_answer'))):.2f}s)")
            print(f"                      avg result bytes read {_avg('result_chars_read'):,.0f}  "
                  f"avg SPARQL chars {_avg('sparql_chars_written'):,.0f}  "
                  f"avg assistant turns {_avg('assistant_turns'):.1f}")
    # head-to-head per case: who was correct, how long, how many tokens
    print("\n  per-case (mark / seconds / attempts / tokens):")
    by_case = {}
    for r in results:
        by_case.setdefault(r["case_id"], {})[r["backend"]] = r
    for cid, m in by_case.items():
        parts = []
        for be in backends:
            if be in m:
                mark = "✓" if m[be]["correct"] else "✗"
                to = "⏱" if m[be].get("timed_out") else ""
                parts.append(f"{be}:{mark}{to}{m[be]['seconds']:.0f}s"
                             f"/a{m[be].get('attempts',1)}"
                             f"/{m[be].get('tokens_total',0):,}tok")
        print(f"    {cid:24s} {'   '.join(parts)}")

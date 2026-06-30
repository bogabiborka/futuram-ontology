"""Provider-agnostic chat client for the bench tool-loop (BYOK / BYOM).

The agent loop in agent.py speaks ONE shape — the Ollama chat response:

    resp = {
        "message": {"content": str, "tool_calls": [
            {"function": {"name": str, "arguments": dict}}, ...]},
        "prompt_eval_count": int,   # input tokens
        "eval_count": int,          # generated tokens
    }

and hands tool results back as messages

    {"role": "tool", "name": <fn>, "content": <text>}

Every adapter here NORMALISES its provider to exactly that shape, so the loop is
identical regardless of provider. Ollama is the default; Anthropic, OpenAI-
compatible (OpenAI / OpenRouter / Together / vLLM / Groq / LM Studio / …) and
Google Gemini are BYOK via an env-var key. Provider SDKs are imported LAZILY
inside each adapter so a missing SDK only breaks that one provider.

Keys are read ONLY from the named environment variable — never logged, never
written into a transcript, results JSON, or the providers config file.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any


# Backoff is intentionally short. A rate-limited run retries a few times over a few
# seconds to absorb a brief blip, then surfaces the error to the loop (provider-error,
# "switch model") rather than waiting out a long Retry-After. Tunable via
# BENCH_BACKOFF_ATTEMPTS / BENCH_BACKOFF_MAX_WAIT for a patient overnight run.
_BACKOFF_ATTEMPTS = int(os.getenv("BENCH_BACKOFF_ATTEMPTS", "3"))
_BACKOFF_MAX_WAIT = float(os.getenv("BENCH_BACKOFF_MAX_WAIT", "8"))

# Per-request HTTP timeout (seconds) on the chat call. Bounds the wait if the
# network drops mid-request: the SDK raises an APITimeout, which _with_backoff
# classifies as transient (a few retries) and, if connectivity does not return,
# surfaces as a provider-error the run loop can act on. Generous enough for a slow
# model to think, but finite. Tunable.
_REQUEST_TIMEOUT = float(os.getenv("BENCH_REQUEST_TIMEOUT", "180"))


async def _with_backoff(make_call, *, attempts: int | None = None,
                        base: float = 1.5, cap: float | None = None):
    """Await `make_call()` (a 0-arg coroutine factory), retrying a FEW times on
    transient provider failures (HTTP 429 rate-limit, 5xx) with short exponential
    backoff. A server `Retry-After` is honoured ONLY if it's short (≤ cap) — a long
    one means the burst window is closed for a while, so we fail fast and let the
    user switch model instead of hanging. Re-raises non-transient errors (auth,
    bad-request) immediately and the last error after the final attempt."""
    attempts = attempts if attempts is not None else _BACKOFF_ATTEMPTS
    cap = cap if cap is not None else _BACKOFF_MAX_WAIT
    last = None
    for i in range(attempts):
        try:
            return await make_call()
        except Exception as e:  # noqa: BLE001
            last = e
            status = getattr(e, "status_code", None)
            name = type(e).__name__
            transient = (status == 429 or (isinstance(status, int) and status >= 500)
                         or "RateLimit" in name or "APIConnection" in name
                         or "InternalServer" in name or "APITimeout" in name
                         # network drop / changed wifi: httpx, ollama and gemini
                         # surface their own *Timeout / Connect* errors, not the
                         # openai-SDK names above — treat any of them as transient.
                         or "Timeout" in name or "ConnectError" in name
                         or "ConnectionError" in name)
            if not transient or i == attempts - 1:
                raise
            wait = min(cap, base ** i) + random.uniform(0, 0.5)
            # a server Retry-After is used only if it's not longer than our cap;
            # a long one = closed window → give up fast rather than hang.
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    ra = resp.headers.get("retry-after")
                    if ra is not None and float(ra) <= cap:
                        wait = float(ra)
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(wait)
    raise last


# Ollama-shaped empty response, so a failed/odd turn can't crash the loop.
def _resp(content: str = "", tool_calls=None, tok_in: int = 0, tok_out: int = 0) -> dict:
    return {
        "message": {"content": content or "", "tool_calls": tool_calls or []},
        "prompt_eval_count": int(tok_in or 0),
        "eval_count": int(tok_out or 0),
    }


def _ollama_tool_calls_from(calls) -> list[dict]:
    """Wrap (name, arguments) pairs into Ollama's tool_call shape."""
    out = []
    for name, args in calls:
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:  # noqa: BLE001
                args = {}
        out.append({"function": {"name": name, "arguments": args or {}}})
    return out


class LLMClient:
    """One async method, returning an Ollama-shaped response dict (see module
    docstring). Subclasses translate their provider's request/response."""

    #: short provider id recorded in the environment probe (no secrets)
    provider = "base"

    def __init__(self, model: str):
        self.model = model

    async def chat(self, messages: list[dict], tools: list[dict],
                   temperature: float = 0.0) -> dict:
        raise NotImplementedError

    def describe(self) -> dict:
        """Non-secret runtime descriptor for the environment record."""
        return {"provider": self.provider, "model": self.model}

    def list_models(self) -> list[str]:
        """Model ids this provider offers (for the UI's model dropdown). Live
        where the provider exposes a list endpoint; a curated list otherwise.
        Returns [] if it can't be determined (UI falls back to free text)."""
        return []


# --------------------------------------------------------------------------- #
# Ollama (default) — local daemon, optionally proxying an *-cloud model.
# --------------------------------------------------------------------------- #
class OllamaAdapter(LLMClient):
    provider = "ollama"

    def __init__(self, model: str, host: str | None = None):
        super().__init__(model)
        from ollama import AsyncClient
        self.host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        # timeout so a network drop raises instead of hanging the run forever.
        self._client = AsyncClient(host=self.host, timeout=_REQUEST_TIMEOUT)

    async def chat(self, messages, tools, temperature: float = 0.0) -> dict:
        # Ollama already returns the canonical shape; pass through, but copy the
        # token fields onto the top level the loop reads.
        resp = await self._client.chat(
            model=self.model, messages=messages, tools=tools,
            options={"temperature": temperature})
        msg = resp.get("message", {}) or {}
        return {
            "message": {"role": "assistant",
                        "content": msg.get("content", "") or "",
                        "tool_calls": msg.get("tool_calls") or []},
            "prompt_eval_count": int(resp.get("prompt_eval_count", 0) or 0),
            "eval_count": int(resp.get("eval_count", 0) or 0),
        }

    def describe(self) -> dict:
        return {"provider": "ollama", "model": self.model, "host": self.host}

    def list_models(self) -> list[str]:
        # the tags installed on the local Ollama daemon
        try:
            from ollama import Client
            models = Client(host=self.host).list().get("models", [])
            return sorted(m.get("model") or m.get("name") for m in models if (m.get("model") or m.get("name")))
        except Exception:  # noqa: BLE001
            return []


# --------------------------------------------------------------------------- #
# Shared message conversion for the API providers (system split, tool results).
# --------------------------------------------------------------------------- #
def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Pull the leading system message(s) out; APIs that take `system` separately
    need them split from the turn list. Joins multiple with blank lines."""
    sys_parts, rest = [], []
    for m in messages:
        if m.get("role") == "system":
            sys_parts.append(m.get("content") or "")
        else:
            rest.append(m)
    return "\n\n".join(p for p in sys_parts if p), rest


# --------------------------------------------------------------------------- #
# Anthropic (Claude) — native Messages API. BYOK: ANTHROPIC_API_KEY.
# --------------------------------------------------------------------------- #
class AnthropicAdapter(LLMClient):
    provider = "anthropic"

    def __init__(self, model: str, api_key: str, base_url: str | None = None,
                 max_tokens: int = 4096):
        super().__init__(model)
        from anthropic import AsyncAnthropic
        kw = {"api_key": api_key}
        if base_url:
            kw["base_url"] = base_url
        self.base_url = base_url
        self.max_tokens = max_tokens
        self._api_key = api_key
        # finite per-request timeout so a network drop raises APITimeout (handled
        # by _with_backoff / the run loop) instead of hanging at "running".
        kw["timeout"] = _REQUEST_TIMEOUT
        self._client = AsyncAnthropic(**kw)

    @staticmethod
    def _tools(tools: list[dict]) -> list[dict]:
        out = []
        for t in tools:
            fn = t.get("function", t)
            out.append({
                "name": fn["name"],
                "description": (fn.get("description") or "")[:1024],
                "input_schema": fn.get("parameters")
                or {"type": "object", "properties": {}},
            })
        return out

    def _messages(self, turns: list[dict]) -> list[dict]:
        """Translate the loop's flat assistant/tool turns into Anthropic content
        blocks. The loop appends, per assistant turn, the raw provider message we
        returned; we stamp a private `_tool_calls` list on it so we can rebuild
        the tool_use blocks here without re-parsing."""
        out: list[dict] = []
        for m in turns:
            role = m.get("role")
            if role == "user":
                out.append({"role": "user", "content": m.get("content") or ""})
            elif role == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in (m.get("tool_calls") or []):
                    fn = tc["function"]
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("_id") or fn["name"],
                        "name": fn["name"],
                        "input": fn.get("arguments") or {},
                    })
                out.append({"role": "assistant",
                            "content": blocks or [{"type": "text", "text": ""}]})
            elif role == "tool":
                # Anthropic tool results are USER-role tool_result blocks keyed by
                # the tool_use id. The loop carries the matching id on `_id`.
                out.append({"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.get("_id") or m.get("name") or "tool",
                    "content": m.get("content") or "",
                }]})
        return out

    async def chat(self, messages, tools, temperature: float = 0.0) -> dict:
        system, turns = _split_system(messages)
        resp = await _with_backoff(lambda: self._client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system or None,
            messages=self._messages(turns), tools=self._tools(tools),
            temperature=temperature))
        content_text = ""
        calls = []  # (name, args, id)
        for block in resp.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                calls.append((block.name, block.input or {}, block.id))
        tool_calls = []
        for name, args, _id in calls:
            tool_calls.append({"function": {"name": name, "arguments": args},
                               "_id": _id})
        usage = resp.usage
        return {
            "message": {"role": "assistant", "content": content_text,
                        "tool_calls": tool_calls},
            "prompt_eval_count": int(getattr(usage, "input_tokens", 0) or 0),
            "eval_count": int(getattr(usage, "output_tokens", 0) or 0),
        }

    def describe(self) -> dict:
        d = {"provider": "anthropic", "model": self.model}
        if self.base_url:
            d["base_url"] = self.base_url
        return d

    def list_models(self) -> list[str]:
        # Anthropic exposes a models list endpoint; use a one-off SYNC client (our
        # self._client is async). Fall back to a curated set on any error.
        try:
            from anthropic import Anthropic
            kw = {"api_key": self._api_key}
            if self.base_url:
                kw["base_url"] = self.base_url
            page = Anthropic(**kw).models.list(limit=100)
            ids = [m.id for m in page.data if getattr(m, "id", None)]
            if ids:
                return sorted(set(ids))
        except Exception:  # noqa: BLE001
            pass
        return ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5",
                "claude-opus-4-7", "claude-sonnet-4-5"]


# --------------------------------------------------------------------------- #
# OpenAI-compatible — OpenAI / OpenRouter / Together / vLLM / Groq / LM Studio…
# Chat-completions is ALREADY Ollama-shaped (tool_calls[].function), so thin.
# BYOK: the configured key_env; BYOM/endpoint: base_url.
# --------------------------------------------------------------------------- #
class OpenAIAdapter(LLMClient):
    provider = "openai"

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        super().__init__(model)
        from openai import AsyncOpenAI
        self.base_url = base_url
        # remember the construction args so list_models() can spin up a SYNC client
        # (the async one's .models.list() can't be consumed from sync code).
        self._api_key = api_key
        self._default_headers = None
        # max_retries=0: _with_backoff is the single retry layer, so the SDK's own
        # long 429 retries do not add minutes of waiting to a throttled run.
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url or None,
                                   max_retries=0, timeout=_REQUEST_TIMEOUT)

    def _sync_client(self):
        from openai import OpenAI
        kw = {"api_key": self._api_key, "base_url": self.base_url or None}
        if self._default_headers:
            kw["default_headers"] = self._default_headers
        return OpenAI(**kw)

    @staticmethod
    def _to_api_messages(messages):
        # The loop's tool messages carry `name` + `content`; chat-completions
        # wants role:"tool" with a tool_call_id. We thread the id we stamped on
        # the assistant turn (`_id`) through the matching tool reply (`_id`).
        api_messages = []
        for m in messages:
            role = m.get("role")
            if role == "assistant" and m.get("tool_calls"):
                api_messages.append({
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [{
                        "id": tc.get("_id") or tc["function"]["name"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": json.dumps(tc["function"].get("arguments") or {}),
                        },
                    } for tc in m["tool_calls"]],
                })
            elif role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": m.get("_id") or m.get("name") or "tool",
                    "content": m.get("content") or "",
                })
            else:
                # default a missing role to assistant (a synthesized turn without
                # tool_calls) so we never emit a null-role message the API rejects
                api_messages.append({"role": role or "assistant",
                                     "content": m.get("content") or ""})
        return api_messages

    #: some Copilot proxy hosts REQUIRE stream=true; subclass flips this on.
    _stream = False

    async def _chat_stream(self, api_messages, tools, temperature):
        """Stream the completion and reassemble it into one non-streamed-looking
        response (content + tool_calls), for endpoints that reject stream:false."""
        stream = await self._client.chat.completions.create(
            model=self.model, messages=api_messages, tools=tools or None,
            temperature=temperature, stream=True,
            stream_options={"include_usage": True})
        content = ""
        tool_acc = {}   # index -> {id, name, args}
        usage = None
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                content += delta.content
            for tc in (getattr(delta, "tool_calls", None) or []):
                slot = tool_acc.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments
        tool_calls = []
        for slot in tool_acc.values():
            try:
                args = json.loads(slot["args"] or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            tool_calls.append({"function": {"name": slot["name"], "arguments": args},
                               "_id": slot["id"]})
        return {
            "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
            "prompt_eval_count": int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
            "eval_count": int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
        }

    async def chat(self, messages, tools, temperature: float = 0.0) -> dict:
        api_messages = self._to_api_messages(messages)
        if self._stream:
            return await _with_backoff(
                lambda: self._chat_stream(api_messages, tools, temperature))

        resp = await _with_backoff(lambda: self._client.chat.completions.create(
            model=self.model, messages=api_messages, tools=tools or None,
            temperature=temperature))
        choice = resp.choices[0].message
        tool_calls = []
        for tc in (choice.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            tool_calls.append({"function": {"name": tc.function.name, "arguments": args},
                               "_id": tc.id})
        usage = resp.usage
        return {
            "message": {"role": "assistant", "content": choice.content or "",
                        "tool_calls": tool_calls},
            "prompt_eval_count": int(getattr(usage, "prompt_tokens", 0) or 0),
            "eval_count": int(getattr(usage, "completion_tokens", 0) or 0),
        }

    def describe(self) -> dict:
        d = {"provider": "openai", "model": self.model}
        if self.base_url:
            d["base_url"] = self.base_url
        return d

    def list_models(self) -> list[str]:
        # OpenAI-compatible /v1/models — covers OpenAI, OpenRouter, Groq, Copilot.
        # Uses a SYNC client (our self._client is async; its paginator can't be
        # consumed from sync code).
        try:
            data = self._sync_client().models.list().data
            ids = [m.id for m in data if getattr(m, "id", None)]
            # drop obvious non-chat models (embeddings) so the dropdown is useful
            ids = [i for i in ids if not any(
                k in i.lower() for k in ("embedding", "embed", "whisper", "tts",
                                         "dall-e", "moderation"))]
            return sorted(set(ids))
        except Exception:  # noqa: BLE001
            return []


# --------------------------------------------------------------------------- #
# GitHub Copilot — OpenAI-compatible chat at api.githubcopilot.com, but it needs
# a short-lived Copilot token (exchanged from a GitHub OAuth/PAT token) and a few
# editor headers. BYOK: either COPILOT_API_KEY (a ready Copilot bearer token) or
# GITHUB_TOKEN / GH_TOKEN (a GitHub token we exchange for the Copilot token).
# --------------------------------------------------------------------------- #
COPILOT_BASE_URL = "https://api.githubcopilot.com"
_COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
# The header set a real Copilot Chat client sends. `openai-intent` +
# `x-github-api-version` are REQUIRED — without them the API returns
# "model not supported" / 404 even for models the account has. Mirrors the
# known-working ericc-ch/copilot-api reference.
_COPILOT_HEADERS = {
    "Editor-Version": "vscode/1.99.3",
    "Editor-Plugin-Version": "copilot-chat/0.26.7",
    "Copilot-Integration-Id": "vscode-chat",
    "openai-intent": "conversation-panel",
    "x-github-api-version": "2025-04-01",
    "user-agent": "GitHubCopilotChat/0.26.7",
    "x-vscode-user-agent-library-version": "electron-fetch",
}


_COPILOT_USER_URL = "https://api.github.com/copilot_internal/user"


def _exchange_copilot_token(github_token: str) -> str:
    """Trade a GitHub OAuth/PAT token for a short-lived Copilot API token."""
    return _exchange_copilot_token_full(github_token)[0]


def _exchange_copilot_token_full(github_token: str) -> tuple[str, str | None]:
    """Trade a GitHub OAuth/PAT token for a (short-lived Copilot API token, api_base).
    The bearer is a `key=value;…` string that carries `proxy-ep` — the account's
    OWN Copilot proxy host (e.g. proxy.individual.githubcopilot.com). The chat API
    MUST go to that host (the generic api.githubcopilot.com rejects the model for
    individual/edu plans), so we derive the base from it."""
    import urllib.request
    req = urllib.request.Request(_COPILOT_TOKEN_URL, headers={
        "Authorization": f"token {github_token}",
        "Editor-Version": _COPILOT_HEADERS["Editor-Version"],
        "User-Agent": "GitHubCopilot/1.0",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    tok = data.get("token")
    if not tok:
        raise SystemExit(
            "GitHub Copilot token exchange returned no token — is the GitHub "
            "token authorised for Copilot? (response had no `token` field)")
    api_base = None
    fields = dict(p.split("=", 1) for p in tok.split(";") if "=" in p)
    ep = fields.get("proxy-ep")  # e.g. proxy.individual.githubcopilot.com
    if ep:
        api_base = ep if ep.startswith("http") else f"https://{ep}"
    return tok, api_base


def copilot_quota(github_token: str) -> dict | None:
    """Fetch the account's live Copilot quota snapshot from the entitlement
    endpoint. Returns a compact dict {plan, quota_reset_date, quotas: {<id>:
    {remaining, entitlement, percent_remaining, unlimited}}} — NO secrets — or
    None on failure. Used by the observer to show the user their budget."""
    import urllib.request
    try:
        req = urllib.request.Request(_COPILOT_USER_URL, headers={
            "Authorization": f"token {github_token}",
            "Editor-Version": _COPILOT_HEADERS["Editor-Version"],
            "User-Agent": "GitHubCopilot/1.0",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None
    snaps = data.get("quota_snapshots") or {}
    quotas = {}
    for qid, q in snaps.items():
        if not isinstance(q, dict):
            continue
        quotas[qid] = {
            "remaining": q.get("remaining"),
            "entitlement": q.get("entitlement"),
            "percent_remaining": q.get("percent_remaining"),
            "unlimited": bool(q.get("unlimited")),
        }
    return {
        "plan": data.get("copilot_plan"),
        "quota_reset_date": data.get("quota_reset_date"),
        "quotas": quotas,
    }


def _copilot_account_api_base(github_token: str) -> str | None:
    """Read the account's OWN Copilot API endpoint from the entitlement payload
    (e.g. https://api.individual.githubcopilot.com for an individual plan).
    Routing to the account-specific host avoids the heavier throttling the
    generic api.githubcopilot.com applies. Returns None on any failure (caller
    falls back to the generic base)."""
    import urllib.request
    try:
        req = urllib.request.Request(_COPILOT_USER_URL, headers={
            "Authorization": f"token {github_token}",
            "Editor-Version": _COPILOT_HEADERS["Editor-Version"],
            "User-Agent": "GitHubCopilot/1.0",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        api = (data.get("endpoints") or {}).get("api")
        return api or None
    except Exception:  # noqa: BLE001
        return None


class CopilotAdapter(OpenAIAdapter):
    """OpenAI-compatible client pinned to the Copilot endpoint, with the editor
    headers Copilot requires. `api_key` is a ready Copilot bearer token; if it
    looks like a GitHub token (gho_/ghp_/ghu_) it is exchanged first."""
    provider = "copilot"
    _stream = True   # the individual/edu Copilot proxy rejects stream:false

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        token = api_key
        if api_key and api_key.startswith(("gho_", "ghp_", "ghu_", "ghs_")):
            token = _exchange_copilot_token(api_key)
        from openai import AsyncOpenAI
        # Chat goes to the GENERIC api.githubcopilot.com (NOT the api.individual/
        # proxy.individual host from the entitlement — those 404 every model). The
        # account is selected by the token + the editor headers, not the host.
        # (Business/Enterprise seats route to api.<accountType>.githubcopilot.com,
        # but an individual/free-edu seat uses the generic host.)
        self.base_url = base_url or COPILOT_BASE_URL
        # NB: call LLMClient.__init__ (set model) but build our own client with
        # the Copilot headers rather than OpenAIAdapter's plain one.
        LLMClient.__init__(self, model)
        # stash for _sync_client() (list_models) so it carries token + headers
        self._api_key = token
        self._default_headers = dict(_COPILOT_HEADERS)
        self._client = AsyncOpenAI(api_key=token, base_url=self.base_url,
                                   default_headers=dict(_COPILOT_HEADERS),
                                   max_retries=0, timeout=_REQUEST_TIMEOUT)

    def describe(self) -> dict:
        return {"provider": "copilot", "model": self.model, "base_url": self.base_url}


# --------------------------------------------------------------------------- #
# Google Gemini — native API. BYOK: GOOGLE_API_KEY.
# --------------------------------------------------------------------------- #
class GeminiAdapter(LLMClient):
    provider = "google"

    def __init__(self, model: str, api_key: str):
        super().__init__(model)
        from google import genai
        self._genai = genai
        # http_options.timeout is in MILLISECONDS; finite so a network drop raises
        # instead of hanging the run (handled by _with_backoff / the run loop).
        self._client = genai.Client(
            api_key=api_key,
            http_options={"timeout": int(_REQUEST_TIMEOUT * 1000)})

    def _tools(self, tools: list[dict]):
        from google.genai import types
        decls = []
        for t in tools:
            fn = t.get("function", t)
            decls.append(types.FunctionDeclaration(
                name=fn["name"],
                description=(fn.get("description") or "")[:1024],
                parameters=fn.get("parameters") or {"type": "object", "properties": {}},
            ))
        return [types.Tool(function_declarations=decls)] if decls else None

    def _contents(self, turns: list[dict]):
        from google.genai import types
        contents = []
        for m in turns:
            role = m.get("role")
            if role == "user":
                contents.append(types.Content(
                    role="user", parts=[types.Part(text=m.get("content") or "")]))
            elif role == "assistant":
                parts = []
                if m.get("content"):
                    parts.append(types.Part(text=m["content"]))
                for tc in (m.get("tool_calls") or []):
                    fn = tc["function"]
                    parts.append(types.Part(function_call=types.FunctionCall(
                        name=fn["name"], args=fn.get("arguments") or {})))
                contents.append(types.Content(role="model", parts=parts or [types.Part(text="")]))
            elif role == "tool":
                contents.append(types.Content(role="user", parts=[types.Part(
                    function_response=types.FunctionResponse(
                        name=m.get("name") or "tool",
                        response={"result": m.get("content") or ""}))]))
        return contents

    async def chat(self, messages, tools, temperature: float = 0.0) -> dict:
        from google.genai import types
        system, turns = _split_system(messages)
        cfg = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system or None,
            tools=self._tools(tools))
        resp = await self._client.aio.models.generate_content(
            model=self.model, contents=self._contents(turns), config=cfg)
        content_text = ""
        tool_calls = []
        cand = (resp.candidates or [None])[0]
        for part in (getattr(cand.content, "parts", None) or []) if cand else []:
            if getattr(part, "text", None):
                content_text += part.text
            fc = getattr(part, "function_call", None)
            if fc:
                tool_calls.append({"function": {"name": fc.name,
                                                "arguments": dict(fc.args or {})},
                                   "_id": fc.name})
        um = getattr(resp, "usage_metadata", None)
        return {
            "message": {"role": "assistant", "content": content_text,
                        "tool_calls": tool_calls},
            "prompt_eval_count": int(getattr(um, "prompt_token_count", 0) or 0),
            "eval_count": int(getattr(um, "candidates_token_count", 0) or 0),
        }

    def list_models(self) -> list[str]:
        # Gemini lists models that support generateContent; strip the "models/"
        # prefix. Curated fallback if the list call fails.
        try:
            out = []
            for m in self._client.models.list():
                actions = getattr(m, "supported_actions", None) or []
                if actions and "generateContent" not in actions:
                    continue
                name = (getattr(m, "name", "") or "").split("/")[-1]
                if name:
                    out.append(name)
            if out:
                return sorted(set(out))
        except Exception:  # noqa: BLE001
            pass
        return ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-2.5-flash"]


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
_PROVIDERS = {"ollama", "anthropic", "openai", "google", "copilot"}


def build_client(provider: str, model: str, *, host: str | None = None,
                 api_key: str | None = None, base_url: str | None = None,
                 max_tokens: int = 4096) -> LLMClient:
    """Construct the adapter for `provider`. API providers require `api_key`
    (raise a clear error naming the env var if missing — caller passes the
    resolved value)."""
    provider = (provider or "ollama").lower()
    if provider == "ollama":
        return OllamaAdapter(model, host=host)
    if provider == "anthropic":
        return AnthropicAdapter(model, api_key=api_key, base_url=base_url,
                                max_tokens=max_tokens)
    if provider in ("openai", "openai-compatible"):
        return OpenAIAdapter(model, api_key=api_key, base_url=base_url)
    if provider == "copilot":
        return CopilotAdapter(model, api_key=api_key, base_url=base_url)
    if provider in ("google", "gemini"):
        return GeminiAdapter(model, api_key=api_key)
    raise SystemExit(
        f"Unknown LLM provider {provider!r}. Known: {', '.join(sorted(_PROVIDERS))}.")

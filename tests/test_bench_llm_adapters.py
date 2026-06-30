"""Unit tests for the bench BYOK/BYOM LLM adapter layer (benchlib/llm.py +
benchlib/report.py provider resolution). No network and no provider SDKs needed:
we drive the PURE conversion methods (request/response normalisation) on adapter
instances built without their __init__, and a tiny fake async client for the
end-to-end chat() shape. The contract under test: every adapter returns the
Ollama-shaped response the agent loop consumes, and no API key ever leaks into
config/descriptors.
"""
import asyncio
import importlib
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
BENCH = REPO / "bench"

# benchlib is a real package (relative imports), so put bench/ on the path and
# import it normally rather than by file path.
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

llm = importlib.import_module("benchlib.llm")


# --------------------------------------------------------------------------- #
# OpenAI-compatible: message + response normalisation (pure, no SDK)
# --------------------------------------------------------------------------- #
class _FakeChatCompletions:
    def __init__(self, captured):
        self._captured = captured

    async def create(self, **kw):
        self._captured.update(kw)

        class _Fn:
            name = "execute_sparql_query"
            arguments = '{"sparql_query": "SELECT * WHERE {?s ?p ?o} LIMIT 1"}'

        class _TC:
            id = "call_123"
            function = _Fn()

        class _Msg:
            content = "thinking"
            tool_calls = [_TC()]

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 11
            completion_tokens = 7

        class _Resp:
            choices = [_Choice()]
            usage = _Usage()

        return _Resp()


def _openai_adapter_without_init():
    a = llm.OpenAIAdapter.__new__(llm.OpenAIAdapter)
    llm.LLMClient.__init__(a, "gpt-4o")
    a.base_url = "https://example/v1"
    return a


def test_openai_chat_normalises_to_ollama_shape():
    a = _openai_adapter_without_init()
    captured = {}

    class _C:
        chat = type("x", (), {"completions": _FakeChatCompletions(captured)})()

    a._client = _C()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "how much copper?"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "execute_sparql_query",
                                      "arguments": {"sparql_query": "ASK{}"}},
                         "_id": "call_1"}]},
        {"role": "tool", "name": "execute_sparql_query", "_id": "call_1",
         "content": "rows..."},
    ]
    resp = asyncio.run(a.chat(messages, tools=[{"function": {
        "name": "execute_sparql_query", "description": "run",
        "parameters": {"type": "object"}}}], temperature=0.0))

    # Ollama-shaped response the loop reads — MUST carry role:"assistant" so that
    # when the loop appends it to `messages`, the NEXT request can rebuild it; a
    # missing role made the API reject the turn (null-role message -> 400).
    assert resp["message"]["role"] == "assistant"
    assert resp["message"]["content"] == "thinking"
    tc = resp["message"]["tool_calls"][0]
    assert tc["function"]["name"] == "execute_sparql_query"
    assert tc["function"]["arguments"]["sparql_query"].startswith("SELECT")
    assert tc["_id"] == "call_123"
    assert resp["prompt_eval_count"] == 11 and resp["eval_count"] == 7

    # request: tool reply threaded back with tool_call_id; assistant tool_calls
    # carry the matching id; system passed through as a role:system message.
    api_msgs = captured["messages"]
    tool_msg = [m for m in api_msgs if m["role"] == "tool"][0]
    assert tool_msg["tool_call_id"] == "call_1"
    asst = [m for m in api_msgs if m["role"] == "assistant"][0]
    assert asst["tool_calls"][0]["id"] == "call_1"
    # EVERY rebuilt message must have a non-null role (a null role -> API 400 that
    # silently killed the tool loop after the first tool call).
    assert all(m.get("role") for m in api_msgs)


def test_openai_message_without_role_defaults_to_assistant():
    # A synthesized assistant turn (no tool_calls, no explicit role) must not
    # serialize to a null-role message.
    a = _openai_adapter_without_init()
    captured = {}

    class _C:
        chat = type("x", (), {"completions": _FakeChatCompletions(captured)})()

    a._client = _C()
    asyncio.run(a.chat([{"content": "no role here"}], tools=[], temperature=0.0))
    assert all(m.get("role") for m in captured["messages"])


# --------------------------------------------------------------------------- #
# Anthropic: message conversion (pure helper, no SDK)
# --------------------------------------------------------------------------- #
def test_anthropic_message_conversion_and_system_split():
    a = llm.AnthropicAdapter.__new__(llm.AnthropicAdapter)
    llm.LLMClient.__init__(a, "claude-opus-4-8")
    a.base_url = None
    messages = [
        {"role": "system", "content": "you are pinned"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "ok",
         "tool_calls": [{"function": {"name": "search", "arguments": {"x": 1}},
                         "_id": "tu_1"}]},
        {"role": "tool", "name": "search", "_id": "tu_1", "content": "result"},
    ]
    system, turns = llm._split_system(messages)
    assert system == "you are pinned"
    conv = a._messages(turns)
    # assistant turn becomes a tool_use block keyed by the id
    asst = [m for m in conv if m["role"] == "assistant"][0]
    tu = [b for b in asst["content"] if b["type"] == "tool_use"][0]
    assert tu["id"] == "tu_1" and tu["name"] == "search"
    # tool result is a user-role tool_result keyed by the same id
    tr = [b for m in conv if m["role"] == "user"
          for b in (m["content"] if isinstance(m["content"], list) else [])
          if isinstance(b, dict) and b.get("type") == "tool_result"][0]
    assert tr["tool_use_id"] == "tu_1"

    # tool schema conversion: function -> input_schema
    tools = a._tools([{"function": {"name": "search", "description": "d",
                                    "parameters": {"type": "object"}}}])
    assert tools[0]["name"] == "search"
    assert tools[0]["input_schema"] == {"type": "object"}


# --------------------------------------------------------------------------- #
# Factory + descriptors never leak a key
# --------------------------------------------------------------------------- #
def test_factory_unknown_provider_raises():
    with pytest.raises(SystemExit):
        llm.build_client("nope", "m", api_key="x")


def test_descriptor_has_no_key():
    a = _openai_adapter_without_init()
    d = a.describe()
    assert d == {"provider": "openai", "model": "gpt-4o",
                 "base_url": "https://example/v1"}
    assert "api_key" not in d and "key" not in d


def test_copilot_base_url_default():
    assert llm.COPILOT_BASE_URL == "https://api.githubcopilot.com"
    assert "Copilot-Integration-Id" in llm._COPILOT_HEADERS


# --------------------------------------------------------------------------- #
# Provider resolution (report.resolve_llm) — selection precedence, no SDK calls
# for ollama (build_client is stubbed) so we don't need the daemon.
# --------------------------------------------------------------------------- #
report = importlib.import_module("benchlib.report")


def test_resolve_llm_bare_tag_stays_ollama(monkeypatch):
    built = {}
    # resolve_llm does `from .llm import build_client`, so patch it on benchlib.llm
    monkeypatch.setattr(llm, "build_client",
                        lambda provider, model, **kw: built.update(
                            provider=provider, model=model) or _Stub(provider, model))
    # the bare-tag path preflights a live Ollama daemon; stub it out so this test
    # checks provider RESOLUTION only (no daemon needed, per this section's intent)
    monkeypatch.setattr(report, "_preflight_ollama", lambda *a, **k: None)
    client, disp, provider = report.resolve_llm("qwen3:32b", None)
    assert provider == "ollama" and built["model"] == "qwen3:32b"


def test_resolve_llm_inline_provider_tag(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "build_client",
                        lambda provider, model, **kw: _Stub(provider, model))
    client, disp, provider = report.resolve_llm(
        "anthropic/claude-opus-4-8", None)
    assert provider == "anthropic" and client.model == "claude-opus-4-8"


def test_resolve_llm_missing_key_errors(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "build_client", lambda *a, **k: _Stub("x", "y"))
    with pytest.raises(SystemExit):
        report.resolve_llm(None, "anthropic")




class _Stub:
    def __init__(self, provider, model):
        self.provider = provider
        self.model = model

    def describe(self):
        return {"provider": self.provider, "model": self.model}

"""MTI_Codex: OpenAI provider adapter (pipeline/ai_provider.py).

The adapter's job is to make ``AI_PROVIDER=openai`` invisible to every call
site (extractor.py, overview_analysis.py, must_meet.py, overview_check.py):
they only ever see Anthropic-shaped requests/responses. These tests exercise
the translation logic directly (no network) plus the provider-selection and
pricing plumbing. No API key or network access required.
"""
import json
from types import SimpleNamespace

import pytest

from pipeline.ai_provider import (
    _image_url_from_block,
    _system_text,
    _tool_call_message,
    _translate_messages,
    _translate_response,
    _translate_tool_choice,
    _translate_tools,
    default_model,
    get_provider,
    is_nonretryable_status,
    is_transient_error,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)


# --------------------------------------------------------------------------- #
# Provider selection
# --------------------------------------------------------------------------- #
def test_default_provider_is_anthropic(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert get_provider() == "anthropic"
    assert default_model() == "claude-sonnet-5"


def test_openai_provider_selected_by_env(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    assert get_provider() == "openai"
    assert default_model() == "gpt-5.6"


def test_provider_env_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "OpenAI")
    assert get_provider() == "openai"


def test_unrecognized_provider_falls_back_to_anthropic(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "some-typo")
    assert default_model() == "claude-sonnet-5"


# --------------------------------------------------------------------------- #
# Transient-error classification (used by both SDKs' exception naming)
# --------------------------------------------------------------------------- #
class _FakeConnectionError(Exception):
    pass


class _FakeStatusError(Exception):
    def __init__(self, status_code):
        super().__init__("status")
        self.status_code = status_code


_FakeConnectionError.__name__ = "APIConnectionError"
_FakeStatusError.__name__ = "APIStatusError"


def test_connection_error_is_transient():
    assert is_transient_error(_FakeConnectionError())
    assert not is_nonretryable_status(_FakeConnectionError())


@pytest.mark.parametrize("code", [429, 500, 502, 503, 529])
def test_transient_status_codes(code):
    e = _FakeStatusError(code)
    assert is_transient_error(e)
    assert not is_nonretryable_status(e)


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_nonretryable_status_codes(code):
    e = _FakeStatusError(code)
    assert not is_transient_error(e)
    assert is_nonretryable_status(e)


def test_unrelated_exception_is_neither():
    e = ValueError("not an API error")
    assert not is_transient_error(e)
    assert not is_nonretryable_status(e)


# --------------------------------------------------------------------------- #
# Message translation: Anthropic content blocks -> OpenAI Chat Completions
# --------------------------------------------------------------------------- #
def test_system_text_handles_string_and_block_list():
    assert _system_text("plain string") == "plain string"
    assert _system_text([{"type": "text", "text": "a"},
                         {"type": "text", "text": "b"}]) == "a\n\nb"
    assert _system_text(None) == ""


def test_image_block_becomes_data_url():
    block = {"type": "image", "source": {"type": "base64",
             "media_type": "image/png", "data": "AAAA"}}
    assert _image_url_from_block(block) == "data:image/png;base64,AAAA"


def test_user_text_and_image_translate_to_content_parts():
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "hello"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": "ZZZZ"}},
    ]}]
    out = _translate_messages(messages)
    assert out == [{"role": "user", "content": [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZZZZ"}},
    ]}]


def test_plain_string_content_passes_through():
    messages = [{"role": "user", "content": "just text"}]
    assert _translate_messages(messages) == [{"role": "user", "content": "just text"}]


def test_assistant_tool_use_becomes_tool_calls():
    block = SimpleNamespace(type="tool_use", id="call_1", name="report_data",
                            input={"a": 1})
    messages = [{"role": "assistant", "content": [block]}]
    out = _translate_messages(messages)
    assert out[0]["role"] == "assistant"
    assert out[0]["tool_calls"][0]["id"] == "call_1"
    assert out[0]["tool_calls"][0]["function"]["name"] == "report_data"
    assert json.loads(out[0]["tool_calls"][0]["function"]["arguments"]) == {"a": 1}


def test_tool_result_becomes_tool_role_message_before_new_content():
    """A low-confidence re-query bundles a tool_result with fresh images/text
    in ONE Anthropic user turn — the adapter must split it: the tool message
    first, then a new user message, matching OpenAI's ordering requirement."""
    messages = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "ack", "is_error": False},
        {"type": "text", "text": "look again"},
    ]}]
    out = _translate_messages(messages)
    assert out[0] == {"role": "tool", "tool_call_id": "call_1", "content": "ack"}
    assert out[1]["role"] == "user"
    assert out[1]["content"] == [{"type": "text", "text": "look again"}]


def test_tool_result_error_is_prefixed():
    messages = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "bad schema",
         "is_error": True},
    ]}]
    out = _translate_messages(messages)
    assert out[0]["content"] == "ERROR: bad schema"


def test_multiple_tool_results_each_become_their_own_message():
    messages = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "a", "is_error": False},
        {"type": "tool_result", "tool_use_id": "call_2", "content": "dup ignored",
         "is_error": True},
    ]}]
    out = _translate_messages(messages)
    assert len(out) == 2
    assert {m["tool_call_id"] for m in out} == {"call_1", "call_2"}


# --------------------------------------------------------------------------- #
# Tool / tool_choice translation
# --------------------------------------------------------------------------- #
def test_tool_schema_translation():
    tools = [{"name": "report", "description": "desc",
             "input_schema": {"type": "object", "properties": {}}}]
    out = _translate_tools(tools)
    assert out == [{"type": "function", "function": {
        "name": "report", "description": "desc",
        "parameters": {"type": "object", "properties": {}},
    }}]


def test_no_tools_translates_to_none():
    assert _translate_tools(None) is None
    assert _translate_tools([]) is None


def test_tool_choice_translation():
    assert _translate_tool_choice({"type": "tool", "name": "report"}) == {
        "type": "function", "function": {"name": "report"}}
    assert _translate_tool_choice(None) is None


# --------------------------------------------------------------------------- #
# Response translation: OpenAI ChatCompletion -> Anthropic-shaped response
# --------------------------------------------------------------------------- #
def _fake_openai_response(*, tool_calls=None, content=None, refusal=None,
                          finish_reason="tool_calls", prompt_tokens=100,
                          completion_tokens=20, cached_tokens=0):
    message = SimpleNamespace(tool_calls=tool_calls, content=content, refusal=refusal)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens))
    return SimpleNamespace(choices=[choice], usage=usage)


def _fake_tool_call(call_id, name, args_dict):
    return SimpleNamespace(id=call_id,
                           function=SimpleNamespace(name=name, arguments=json.dumps(args_dict)))


def test_tool_call_translates_to_tool_use_block():
    resp = _fake_openai_response(tool_calls=[_fake_tool_call("call_1", "report", {"x": 1})])
    out = _translate_response(resp)
    assert len(out.content) == 1
    assert out.content[0].type == "tool_use"
    assert out.content[0].id == "call_1"
    assert out.content[0].name == "report"
    assert out.content[0].input == {"x": 1}
    assert out.stop_reason == "tool_use"


def test_usage_excludes_cached_from_input_tokens():
    """Mirrors Anthropic's convention: input_tokens is the UNCACHED portion,
    cache_read_input_tokens is the cached portion — together they sum to the
    model's total prompt tokens, so estimate_cost() prices each correctly."""
    resp = _fake_openai_response(tool_calls=[_fake_tool_call("c", "t", {})],
                                 prompt_tokens=1000, completion_tokens=50,
                                 cached_tokens=300)
    out = _translate_response(resp)
    assert out.usage.input_tokens == 700
    assert out.usage.cache_read_input_tokens == 300
    assert out.usage.cache_creation_input_tokens == 0
    assert out.usage.output_tokens == 50


def test_malformed_tool_arguments_become_empty_dict_not_a_crash():
    bad_call = SimpleNamespace(id="c1", function=SimpleNamespace(name="report",
                                                                 arguments="{not json"))
    resp = _fake_openai_response(tool_calls=[bad_call])
    out = _translate_response(resp)
    assert out.content[0].input == {}  # fails the caller's Pydantic validation instead


def test_refusal_maps_to_refusal_stop_reason():
    resp = _fake_openai_response(tool_calls=None, refusal="I can't help with that",
                                 finish_reason="stop")
    out = _translate_response(resp)
    assert out.stop_reason == "refusal"


def test_plain_text_response_with_no_tool_calls():
    resp = _fake_openai_response(tool_calls=None, content="just some text",
                                 finish_reason="stop")
    out = _translate_response(resp)
    assert out.content[0].type == "text"
    assert out.content[0].text == "just some text"
    assert out.stop_reason == "end_turn"


def test_length_finish_reason_maps_to_max_tokens():
    resp = _fake_openai_response(tool_calls=None, content="truncated", finish_reason="length")
    out = _translate_response(resp)
    assert out.stop_reason == "max_tokens"


# --------------------------------------------------------------------------- #
# Client construction (no network — just key-presence/error-path checks)
# --------------------------------------------------------------------------- #
def test_build_client_openai_requires_key(monkeypatch):
    from pipeline.ai_provider import build_client

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="OPENAI_API_KEY"):
        build_client(3)


def test_build_client_openai_returns_adapter_with_key(monkeypatch):
    from pipeline.ai_provider import build_client

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    client = build_client(3)
    assert hasattr(client, "messages")
    assert hasattr(client.messages, "create")


def test_extractor_default_model_follows_provider(monkeypatch):
    """extractor.DEFAULT_MODEL is a module-level constant computed ONCE at
    import time (matching the original ``DEFAULT_MODEL = "claude-sonnet-5"``
    plain-constant design) via ``_default_model()``. Test that underlying
    function directly under both env states rather than reloading the module:
    ``importlib.reload`` would replace class identities module-wide (e.g.
    ExtractionError becomes a NEW class object), silently breaking
    ``pytest.raises(ExtractionError)`` in every other test that imported the
    old class reference before the reload — a real test-pollution hazard,
    not a hypothetical one."""
    import pipeline.extractor as extractor_module

    monkeypatch.setenv("AI_PROVIDER", "openai")
    assert extractor_module._default_model() == "gpt-5.6"
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    assert extractor_module._default_model() == "claude-sonnet-5"


def test_usage_log_pricing_includes_gpt_5_6_tiers():
    from pipeline.usage_log import PRICING, estimate_cost

    assert PRICING["gpt-5.6"]["input"] == 5.00
    assert PRICING["gpt-5.6"]["output"] == 30.00
    usage = {"input_tokens": 1000, "output_tokens": 1000, "cache_read_input_tokens": 0}
    cost = estimate_cost(usage, "gpt-5.6")
    assert cost == pytest.approx(0.005 + 0.030)


# --------------------------------------------------------------------------- #
# Provider status (REFACTOR_ANALYSIS §2.2)
# --------------------------------------------------------------------------- #
def test_anthropic_is_the_production_verified_path(monkeypatch):
    from pipeline.ai_provider import provider_status

    monkeypatch.delenv("AI_PROVIDER", raising=False)
    st = provider_status()
    assert st["provider"] == "anthropic"
    assert st["production_verified"] is True
    assert st["note"] == ""


def test_openai_status_states_it_is_not_production_verified(monkeypatch):
    from pipeline.ai_provider import provider_status

    monkeypatch.setenv("AI_PROVIDER", "openai")
    st = provider_status()
    assert st["provider"] == "openai"
    assert st["status"] == "adapter_tested"
    assert st["production_verified"] is False
    assert "NOT been verified end-to-end" in st["note"]
    assert st["model"] == "gpt-5.6"


def test_unknown_provider_is_reported_as_unrecognized(monkeypatch):
    from pipeline.ai_provider import provider_status

    monkeypatch.setenv("AI_PROVIDER", "mistral")
    st = provider_status()
    assert st["recognized"] is False
    assert st["provider"] == "anthropic"      # falls back, as build_client does


def test_selecting_an_unverified_provider_warns_once(monkeypatch, caplog):
    import pipeline.ai_provider as ap

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(ap, "_status_warned", set())
    monkeypatch.setattr(ap, "_build_openai_client", lambda n: "CLIENT")
    with caplog.at_level("WARNING"):
        assert ap.build_client(1) == "CLIENT"
        assert ap.build_client(1) == "CLIENT"
    warnings = [r for r in caplog.records if "NOT been verified" in r.getMessage()]
    assert len(warnings) == 1          # once per process, not once per call


# --------------------------------------------------------------------------- #
# Call-site contract: every LLM stage must go through the shared client contract
# so AI_PROVIDER genuinely swaps the model without touching any call site.
# These drive the REAL stage functions through the OpenAI adapter over a fake
# OpenAI SDK client — no network, no key, no Anthropic import.
# --------------------------------------------------------------------------- #
class _FakeCompletions:
    """Records the OpenAI-shaped request and returns a canned tool call."""

    def __init__(self, tool_name, payload):
        self.tool_name, self.payload = tool_name, payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _fake_openai_response(
            tool_calls=[_fake_tool_call("call_1", self.tool_name, self.payload)])


class _FakeOpenAI:
    def __init__(self, tool_name, payload):
        self.completions = _FakeCompletions(tool_name, payload)
        self.chat = SimpleNamespace(completions=self.completions)


def _adapter_for(tool_name, payload):
    from pipeline.ai_provider import _OpenAIAdapterClient

    raw = _FakeOpenAI(tool_name, payload)
    return _OpenAIAdapterClient(raw), raw.completions


class TestCallSiteContract:
    """REFACTOR_ANALYSIS §2.2: the adapter is only worth its abstraction cost if
    every stage really does share ONE client contract. Each test runs a real
    stage entry point against the adapter and asserts a well-formed OpenAI
    request came out the other side."""

    def test_overview_analysis_stage_runs_through_the_adapter(self, monkeypatch):
        """Stage 1.5 — the holistic overview pass."""
        import pipeline.extractor as ex
        import pipeline.overview_analysis as oa

        client, completions = _adapter_for(oa.TOOL_NAME, {
            "part_number": "T-1", "views_detected": [],
            "cross_view_correspondences": [], "cross_view_conflicts": [],
            "global_notes": [], "overall_shape_summary": "flat plate",
        })
        monkeypatch.setattr(ex, "_build_client", lambda *a, **k: client)
        out = oa.analyze_overview("aGVsbG8=", model="gpt-5.6", cache_dir=None)
        assert out is not None and out["overall_shape_summary"] == "flat plate"
        req = completions.calls[0]
        assert req["model"] == "gpt-5.6"
        assert req["tools"][0]["function"]["name"] == oa.TOOL_NAME
        assert req["reasoning_effort"] == "none"

    def test_overview_image_check_runs_through_the_adapter(self, monkeypatch):
        from pipeline import overview_validate as ov

        client, completions = _adapter_for(
            ov.OVERVIEW_TOOL_NAME,
            {"features": [{"kind": "hole", "count": 4, "description": "mounting"}]})
        import pipeline.extractor as ex

        monkeypatch.setattr(ex, "_build_client", lambda *a, **k: client)
        data = ov.extract_overview_features("aGVsbG8=", media_type="image/png",
                                            model="gpt-5.6", cache_dir=None)
        assert data["features"][0]["kind"] == "hole"
        req = completions.calls[0]
        # Anthropic-shaped call translated correctly for OpenAI:
        assert req["model"] == "gpt-5.6"
        assert req["max_completion_tokens"] == ov.MAX_TOKENS
        assert req["messages"][0]["role"] == "system"
        assert req["tools"][0]["function"]["name"] == ov.OVERVIEW_TOOL_NAME
        assert req["tool_choice"]["function"]["name"] == ov.OVERVIEW_TOOL_NAME
        # forced tool call on a reasoning model => reasoning_effort disabled
        assert req["reasoning_effort"] == "none"
        # the image really crossed as a data URL
        parts = req["messages"][1]["content"]
        assert any(p["type"] == "image_url" for p in parts)

    def test_must_meet_spec_parsing_runs_through_the_adapter(self, monkeypatch):
        """Stage 2.6 — operator must-meet spec parsing."""
        import pipeline.extractor as ex
        from pipeline import must_meet as mm

        client, completions = _adapter_for(mm._MM_TOOL_NAME, {"constraints": [
            {"id": "MM-001", "kind": "hole_count", "text": "4 holes required"}]})
        monkeypatch.setattr(ex, "_build_client", lambda *a, **k: client)
        got = mm.parse_spec_text_llm("Part must have 4 holes.")
        assert got and got[0]["id"] == "MM-001"
        req = completions.calls[0]
        assert req["tools"][0]["function"]["name"] == mm._MM_TOOL_NAME
        assert req["tool_choice"]["function"]["name"] == mm._MM_TOOL_NAME

    def test_usage_translation_feeds_the_shared_cost_ledger(self):
        """Whatever the provider, usage must reach usage_log in Anthropic's
        accounting convention — otherwise cost reporting silently lies."""
        from pipeline.ai_provider import _translate_response
        from pipeline.usage_log import estimate_cost

        resp = _translate_response(_fake_openai_response(
            content="ok", finish_reason="stop", prompt_tokens=1000,
            completion_tokens=200, cached_tokens=400))
        assert resp.usage.input_tokens == 600
        assert resp.usage.cache_read_input_tokens == 400
        assert resp.usage.cache_creation_input_tokens == 0
        cost = estimate_cost({
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "cache_read_input_tokens": resp.usage.cache_read_input_tokens,
            "cache_creation_input_tokens": 0}, "gpt-5.6")
        assert cost > 0

"""AnthropicLLM against a fake client: request shape, text extraction, refusals, error mapping, key handling."""
import types

import anthropic
import pytest

from subsweep.ai import AnthropicLLM, LLMRefused, LLMUnavailable, build_default_llm
from subsweep.ai.llm import DEFAULT_MODEL, FALLBACK_BETA


class Resp:
    def __init__(self, text="Hello there.", stop_reason="end_turn", content=None, model=DEFAULT_MODEL):
        self.content = content if content is not None else [types.SimpleNamespace(type="thinking", thinking=""), types.SimpleNamespace(type="text", text=text)]
        self.stop_reason, self.model, self._request_id = stop_reason, model, "req_123"
        self.usage = types.SimpleNamespace(input_tokens=11, output_tokens=7, cache_read_input_tokens=0)
        self.stop_details = types.SimpleNamespace(category="cyber")


class Endpoint:
    def __init__(self, script):
        self.script, self.calls = script, []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(r, Exception):
            raise r
        return r


class FakeClient:
    def __init__(self, script):
        self.messages = Endpoint(script)
        self.beta = types.SimpleNamespace(messages=Endpoint(script))


class HttpResp:
    def __init__(self, code):
        self.status_code, self.headers, self.request, self.text = code, {"request-id": "req_e"}, types.SimpleNamespace(), ""


def err(cls, code, msg="boom"):
    return cls(msg, response=HttpResp(code), body=None)


def llm(script, **kw):
    c = FakeClient(script if isinstance(script, list) else [script])
    return AnthropicLLM(client=c, **kw), c


def test_default_request_shape_for_the_current_model():
    m, c = llm(Resp())
    out = m.complete(system="SYS", user="USER", max_tokens=900, feature="explain")
    kw = c.beta.messages.calls[0]
    assert kw["model"] == "claude-opus-5-5" == DEFAULT_MODEL
    assert kw["system"] == "SYS" and kw["messages"] == [{"role": "user", "content": "USER"}] and kw["max_tokens"] == 900
    assert kw["output_config"] == {"effort": "low"}
    assert kw["betas"] == [FALLBACK_BETA] == ["server-side-fallback-2026-07-01"] and kw["extra_body"] == {"fallbacks": "default"}
    assert out.text == "Hello there." and out.stop_reason == "end_turn" and out.request_id == "req_123"
    assert out.usage == {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 0}


@pytest.mark.parametrize("banned", ["temperature", "top_p", "top_k", "thinking", "tool_choice"])
def test_parameters_this_model_family_rejects_are_never_sent(banned):
    m, c = llm(Resp())
    m.complete(system="s", user="u")
    assert banned not in c.beta.messages.calls[0]


def test_only_text_blocks_are_returned_thinking_is_ignored():
    content = [types.SimpleNamespace(type="thinking", thinking="secret", text="LEAK"), types.SimpleNamespace(type="text", text="Part one. "),
               types.SimpleNamespace(type="text", text="Part two.")]
    m, _ = llm(Resp(content=content))
    assert m.complete(system="s", user="u").text == "Part one. Part two." 


def test_model_is_configurable_by_argument_and_environment(monkeypatch):
    assert llm(Resp(), model="claude-sonnet-5-5")[0].model == "claude-sonnet-5-5"
    monkeypatch.setenv("SUBSWEEP_MODEL", "claude-haiku-5-5")
    assert llm(Resp())[0].model == "claude-haiku-5-5"


def test_fallbacks_can_be_switched_off_and_then_use_the_plain_endpoint():
    m, c = llm(Resp(), use_fallbacks=False)
    m.complete(system="s", user="u")
    assert c.messages.calls and not c.beta.messages.calls and "extra_body" not in c.messages.calls[0]


def test_rejected_fallbacks_option_is_dropped_once_and_remembered():
    c = FakeClient([err(anthropic.BadRequestError, 400, "unknown beta server-side-fallback")])
    c.messages = Endpoint([Resp("fine")])
    m = AnthropicLLM(client=c)
    assert m.complete(system="s", user="u").text == "fine" and m.use_fallbacks is False
    m.complete(system="s", user="u")
    assert len(c.beta.messages.calls) == 1 and len(c.messages.calls) == 2           # later calls skip the beta path


def test_other_bad_requests_are_not_retried_blindly_but_still_become_friendly_errors():
    m, c = llm(err(anthropic.BadRequestError, 400, "max_tokens too large"))
    with pytest.raises(LLMUnavailable) as e:
        m.complete(system="s", user="u")
    assert m.use_fallbacks is True and e.value.kind == "server" and len(c.beta.messages.calls) == 1 and not c.messages.calls


def test_refusal_stop_reason_raises_llm_refused_with_category():
    m, _ = llm(Resp(stop_reason="refusal", content=[]))
    with pytest.raises(LLMRefused) as e:
        m.complete(system="s", user="u")
    assert e.value.category == "cyber"


@pytest.mark.parametrize("exc,kind,words", [
    (err(anthropic.AuthenticationError, 401), "auth", "key was rejected"),
    (err(anthropic.PermissionDeniedError, 403), "permission", "isn't allowed"),
    (err(anthropic.RateLimitError, 429), "rate_limit", "busy"),
    (anthropic.APIConnectionError(request=types.SimpleNamespace()), "network", "Couldn't reach"),
    (err(anthropic.InternalServerError, 500), "server", "status 500"),
])
def test_api_errors_become_friendly_unavailable_errors(exc, kind, words):
    m, _ = llm(exc)
    with pytest.raises(LLMUnavailable) as e:
        m.complete(system="s", user="u")
    assert e.value.kind == kind and words in e.value.user_message and "sk-ant" not in e.value.user_message


def test_missing_key_is_a_friendly_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable) as e:
        AnthropicLLM()
    assert e.value.kind == "no_key" and "ANTHROPIC_API_KEY" in e.value.user_message


def test_key_is_read_from_the_environment_and_never_shown(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-REALLYSECRET")
    m = AnthropicLLM()
    assert "REALLYSECRET" not in repr(m) and "REALLYSECRET" not in str(m.__dict__.get("model"))


def test_build_default_llm_respects_missing_key_and_the_off_switch(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SUBSWEEP_AI", raising=False)
    assert build_default_llm() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-x1234567")
    assert build_default_llm() is not None
    monkeypatch.setenv("SUBSWEEP_AI", "off")
    assert build_default_llm() is None

"""Thin Anthropic client wrapper. The rest of the AI layer only sees the `LLM` protocol, so tests use a fake.

Model: claude-opus-5-5 by default (override with SUBSWEEP_MODEL, e.g. claude-sonnet-5-5 or claude-haiku-5-5 for lower
cost). Notes for this model family: thinking is always on and cannot be disabled, `temperature`/`top_p` are not
accepted, and effort defaults to `medium`, so we set `output_config.effort` explicitly (low: these are short tasks).
Refusals: the server-side `fallbacks: "default"` option (beta server-side-fallback-2026-07-01) is requested by default
via extra_body; if the API rejects it we retry once without it and stop sending it for this client.
The API key comes only from ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(Exception):
    """The model could not be used (no key, rejected key, network, rate limit). `user_message` is UI-safe."""

    def __init__(self, user_message: str, *, kind: str = "unavailable"):
        super().__init__(user_message)
        self.user_message, self.kind = user_message, kind


class LLMRefused(Exception):
    """The model declined the request (stop_reason == 'refusal')."""

    def __init__(self, category: str | None = None):
        super().__init__("The model declined this request.")
        self.category = category


@dataclass
class LLMResult:
    text: str
    model: str = ""
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)
    request_id: str | None = None
    latency_ms: int = 0


class LLM(Protocol):
    model: str

    def complete(self, *, system: str, user: str, max_tokens: int = 1024, feature: str = "") -> LLMResult: ...


class AnthropicLLM:
    def __init__(self, api_key: str | None = None, model: str | None = None, *, client=None, timeout: float = 45.0,
                 max_retries: int = 3, use_fallbacks: bool = True, effort: str = "low"):
        import anthropic

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if client is None and not key:
            raise LLMUnavailable("The AI assistant isn't connected: add ANTHROPIC_API_KEY to your .env file.", kind="no_key")
        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=max_retries)
        self.model = model or os.environ.get("SUBSWEEP_MODEL") or DEFAULT_MODEL
        self.use_fallbacks, self.effort = use_fallbacks, effort

    def __repr__(self) -> str:
        return f"AnthropicLLM(model={self.model!r})"          # never includes the key

    def _create(self, **kw):
        if self.use_fallbacks:
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], extra_body={"fallbacks": "default"}, **kw)
        return self.client.messages.create(**kw)

    def complete(self, *, system: str, user: str, max_tokens: int = 1024, feature: str = "") -> LLMResult:
        a = self._anthropic
        kw = dict(model=self.model, max_tokens=max_tokens, system=system, output_config={"effort": self.effort},
                  messages=[{"role": "user", "content": user}])
        t0 = time.monotonic()
        try:
            try:
                resp = self._create(**kw)
            except a.BadRequestError as exc:
                msg = str(exc).lower()
                if self.use_fallbacks and ("fallback" in msg or "beta" in msg):      # feature not accepted: drop it, retry once
                    self.use_fallbacks = False
                    resp = self._create(**kw)
                else:
                    raise
        except a.AuthenticationError:
            raise LLMUnavailable("The AI key was rejected. Check ANTHROPIC_API_KEY in your .env file.", kind="auth") from None
        except a.PermissionDeniedError:
            raise LLMUnavailable("This API key isn't allowed to use that model.", kind="permission") from None
        except a.RateLimitError:
            raise LLMUnavailable("The AI service is busy right now. Showing the built-in explanation instead.", kind="rate_limit") from None
        except a.APIConnectionError:
            raise LLMUnavailable("Couldn't reach the AI service. Showing the built-in explanation instead.", kind="network") from None
        except a.APIStatusError as exc:
            raise LLMUnavailable(f"The AI service had a problem (status {exc.status_code}). Showing the built-in explanation instead.",
                                 kind="server") from None
        if getattr(resp, "stop_reason", "") == "refusal":
            details = getattr(resp, "stop_details", None)
            raise LLMRefused(getattr(details, "category", None))
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        u = getattr(resp, "usage", None)
        usage = {k: getattr(u, k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens") if u is not None and hasattr(u, k)}
        return LLMResult(text=text, model=getattr(resp, "model", self.model), stop_reason=getattr(resp, "stop_reason", "") or "",
                         usage=usage, request_id=getattr(resp, "_request_id", None), latency_ms=int((time.monotonic() - t0) * 1000))


def build_default_llm() -> "AnthropicLLM | None":
    """The real client if a key is configured (and SUBSWEEP_AI isn't 'off'); otherwise None => template mode."""
    from subsweep.paypal.config import load_dotenv

    load_dotenv()
    if (os.environ.get("SUBSWEEP_AI") or "").strip().lower() == "off":
        return None
    try:
        return AnthropicLLM()
    except LLMUnavailable:
        return None

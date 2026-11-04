"""PayPal REST client: OAuth client-credentials with a cached token, retries, and friendly errors.

Verified against PayPal's docs (developer.paypal.com):
  * POST /v1/oauth2/token, HTTP Basic (client id/secret), form body grant_type=client_credentials;
    response has access_token, token_type, expires_in (seconds), scope (space separated).
  * 401 -> credentials/token rejected. 403 -> insufficient permissions (we name the scope when our own
    token's scope list shows it is missing). 429 / 5xx are retried with exponential backoff.
Reads (GET) run freely. Any other method needs confirmed=True, or ConfirmationRequired is raised.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

import httpx

from .config import PayPalConfig
from .errors import (
    ConfirmationRequired, PayPalAuthError, PayPalError, PayPalNotFound, PayPalPermissionError,
    PayPalRateLimited, PayPalUnavailable,
)

TOKEN_PATH = "/v1/oauth2/token"
REFRESH_MARGIN_S = 60          # refresh this long before the token expires
SAFE_METHODS = {"GET", "HEAD"}


class PayPalClient:
    def __init__(
        self,
        config: PayPalConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_retries: int = 4,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        timeout: float = 20.0,
    ):
        self.config = config
        self._http = httpx.Client(base_url=config.api_base, transport=transport, timeout=timeout)
        self._sleep, self._clock = sleep, clock
        self.max_retries, self.backoff_base, self.backoff_cap = max_retries, backoff_base, backoff_cap
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self.scopes: set[str] = set()
        self.token_requests = 0           # exposed for tests / diagnostics

    def __repr__(self) -> str:
        return f"PayPalClient({self.config!r})"

    def close(self) -> None:
        self._http.close()

    # ---------------------------------------------------------------- retries
    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        delay = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 30.0))
            except ValueError:
                pass
        return delay

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        """One logical request with backoff on 429 / 5xx / network errors. Returns the final response."""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._http.request(method, path, **kw)
            except httpx.TransportError as exc:      # timeouts, connection resets, DNS
                last_exc = exc
                resp = None
            if resp is not None and resp.status_code != 429 and resp.status_code < 500:
                return resp
            if attempt == self.max_retries:
                break
            self._sleep(self._backoff(attempt, resp.headers.get("Retry-After") if resp is not None else None))
        if resp is not None and resp.status_code == 429:
            raise PayPalRateLimited("PayPal is asking us to slow down. Please try again in a minute.", status=429)
        raise PayPalUnavailable(
            "PayPal isn't responding right now. Your data is safe; please try again shortly.",
            status=resp.status_code if resp is not None else None,
        ) from last_exc

    # ---------------------------------------------------------------- auth
    def _fetch_token(self) -> None:
        resp = self._send(
            "POST", TOKEN_PATH,
            auth=(self.config.client_id, self.config.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json", "Accept-Language": "en_US"},
        )
        self.token_requests += 1
        if resp.status_code in (401, 403):
            raise PayPalAuthError(
                "PayPal didn't accept the client ID and secret. Check PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET "
                "belong to the same sandbox app.", status=resp.status_code, name="invalid_client")
        if resp.status_code >= 400:
            raise self._error_from(resp, None)
        body = resp.json()
        self._token = body["access_token"]
        self._expires_at = self._clock() + float(body.get("expires_in", 0)) - REFRESH_MARGIN_S
        self.scopes = set((body.get("scope") or "").split())

    def get_token(self) -> str:
        with self._lock:
            if self._token is None or self._clock() >= self._expires_at:
                self._fetch_token()
            return self._token  # type: ignore[return-value]

    def _invalidate(self) -> None:
        with self._lock:
            self._token, self._expires_at = None, 0.0

    # ---------------------------------------------------------------- errors
    def _error_from(self, resp: httpx.Response, required_scope: str | None) -> PayPalError:
        try:
            body: Any = resp.json()
        except ValueError:
            body = {}
        name, debug_id = body.get("name") or body.get("error"), body.get("debug_id")
        details = body.get("details") or []
        common = dict(status=resp.status_code, name=name, debug_id=debug_id, details=details)
        issue = (details[0].get("issue") or details[0].get("description")) if details and isinstance(details[0], dict) else None
        if resp.status_code == 401:
            return PayPalAuthError("PayPal rejected our sign-in. We'll fetch a fresh token next time; if this keeps "
                                   "happening, re-check your sandbox client ID and secret.", **common)
        if resp.status_code == 403:
            if required_scope and self.scopes and required_scope not in self.scopes:
                return PayPalPermissionError(
                    f"Your PayPal app can't use this feature yet: its access token doesn't include the scope "
                    f"{required_scope}. Enable that feature for the app in the PayPal Developer Dashboard "
                    "(Apps & Credentials), then try again.", missing_scope=required_scope, **common)
            return PayPalPermissionError(
                "PayPal says this app isn't permitted to do that" + (f" ({issue})" if issue else "") + ". "
                "Check the app's features and that you're using a sandbox business app.",
                missing_scope=required_scope, **common)
        if resp.status_code == 404:
            return PayPalNotFound("PayPal couldn't find that item. It may have been removed or belong to another app.", **common)
        msg = body.get("message") or "PayPal couldn't complete that request."
        return PayPalError(f"{msg}" + (f" ({issue})" if issue else "") + (f" [PayPal debug id {debug_id}]" if debug_id else ""), **common)

    # ---------------------------------------------------------------- requests
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        headers: dict | None = None,
        required_scope: str | None = None,
        confirmed: bool = False,
    ) -> dict:
        method = method.upper()
        if method not in SAFE_METHODS and not confirmed:
            raise ConfirmationRequired(f"{method} {path}")
        refreshed = False
        while True:
            token = self.get_token()
            hdrs = {"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})}
            resp = self._send(method, path, params=params, json=json, headers=hdrs)
            if resp.status_code == 401 and not refreshed:      # token may have been revoked/expired early
                self._invalidate()
                refreshed = True
                continue
            if resp.status_code >= 400:
                raise self._error_from(resp, required_scope)
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()

    def get(self, path: str, **kw) -> dict:
        return self.request("GET", path, **kw)

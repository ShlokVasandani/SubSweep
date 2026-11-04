"""Helpers: a scriptable fake PayPal (httpx.MockTransport) plus fake clock/sleep for the client."""
import json
from datetime import date

import httpx

from subsweep.paypal import PayPalClient, PayPalConfig

CFG = PayPalConfig("CLIENT_ID_123456", "SUPER_SECRET_VALUE", "https://api-m.sandbox.paypal.com")
SEARCH_SCOPE = "https://uri.paypal.com/services/reporting/search/read"
SUBS_SCOPE = "https://uri.paypal.com/services/subscriptions"


def token_body(expires_in=3600, scope=SUBS_SCOPE + " openid", token="tok-1"):
    return {"access_token": token, "token_type": "Bearer", "expires_in": expires_in, "scope": scope, "app_id": "APP-1"}


class FakePayPal:
    """route(method, path) -> list of responses (consumed in order; the last one repeats)."""

    def __init__(self):
        self.routes: dict[tuple[str, str], list] = {}
        self.requests: list[httpx.Request] = []
        self.token_counter = 0
        self.token_responses: list = []

    def on(self, method, path, *responses):
        self.routes[(method, path)] = list(responses)
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key == ("POST", "/v1/oauth2/token"):
            self.token_counter += 1
            if self.token_responses:
                r = self.token_responses.pop(0) if len(self.token_responses) > 1 else self.token_responses[0]
                return self._resp(r)
            return httpx.Response(200, json=token_body(token=f"tok-{self.token_counter}"))
        queue = self.routes.get(key)
        if not queue:
            return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "nope"})
        r = queue.pop(0) if len(queue) > 1 else queue[0]
        return self._resp(r)

    @staticmethod
    def _resp(r):
        if isinstance(r, Exception):
            raise r
        if isinstance(r, httpx.Response):
            return r
        if isinstance(r, tuple):
            status, body, *rest = r
            return httpx.Response(status, json=body, headers=rest[0] if rest else None)
        return httpx.Response(200, json=r)

    def api_requests(self):
        return [r for r in self.requests if r.url.path != "/v1/oauth2/token"]


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)


def make(fake: FakePayPal, **kw):
    clock = Clock()
    client = PayPalClient(CFG, transport=httpx.MockTransport(fake.handler), sleep=clock.sleep, clock=clock.now, **kw)
    return client, clock


def detail(code="T0002", status="S", value="10.99", cur="USD", when="2026-10-05T10:00:00+0000", tid="TX1",
           subject=None, item=None, sub_id=None, payer=("Pat", "Buyer"), note=None):
    info = {"transaction_id": tid, "transaction_event_code": code, "transaction_status": status,
            "transaction_initiation_date": when, "transaction_amount": {"currency_code": cur, "value": value}}
    if subject:
        info["transaction_subject"] = subject
    if note:
        info["transaction_note"] = note
    if sub_id:
        info["paypal_reference_id"], info["paypal_reference_id_type"] = sub_id, "SUB"
    d = {"transaction_info": info, "payer_info": {"payer_name": {"given_name": payer[0], "surname": payer[1]}}}
    if item:
        d["cart_info"] = {"item_details": [{"item_name": item}]}
    return d

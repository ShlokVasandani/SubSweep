"""OAuth, token caching, retries, error mapping and the confirmation gate. All HTTP is mocked."""
import base64
import logging
import os

import httpx
import pytest

from subsweep.paypal import (
    ConfirmationRequired, PayPalAuthError, PayPalConfig, PayPalError, PayPalNotConfigured, PayPalNotFound,
    PayPalPermissionError, PayPalRateLimited, PayPalUnavailable,
)
from subsweep.paypal.config import load_dotenv
from tests.paypal_helpers import CFG, SEARCH_SCOPE, SUBS_SCOPE, FakePayPal, make, token_body

OK = {"ok": True}


# ---------------------------------------------------------------- config
def test_missing_credentials_is_a_friendly_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(PayPalNotConfigured, match="isn't connected yet"):
        PayPalConfig.from_env()
    assert PayPalConfig.from_env(require=False) is None


def test_config_reads_environment_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "abc12345")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "s3cret")
    monkeypatch.delenv("PAYPAL_API_BASE", raising=False)
    c = PayPalConfig.from_env()
    assert c.api_base == "https://api-m.sandbox.paypal.com" and c.is_sandbox


def test_repr_never_shows_the_secret():
    assert "SUPER_SECRET_VALUE" not in repr(CFG) and "SUPER_SECRET_VALUE" not in repr(make(FakePayPal())[0])


def test_live_api_base_refused_unless_explicitly_allowed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "a")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "b")
    monkeypatch.setenv("PAYPAL_API_BASE", "https://api-m.paypal.com")
    with pytest.raises(PayPalNotConfigured, match="sandbox-only"):
        PayPalConfig.from_env()
    monkeypatch.setenv("SUBSWEEP_ALLOW_LIVE", "1")
    assert not PayPalConfig.from_env().is_sandbox


def test_non_https_base_refused(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "a")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "b")
    monkeypatch.setenv("PAYPAL_API_BASE", "http://api-m.sandbox.paypal.com")
    with pytest.raises(PayPalNotConfigured, match="https"):
        PayPalConfig.from_env()


def test_dotenv_loads_but_never_overrides_real_env(monkeypatch, tmp_path):
    f = tmp_path / ".env"
    f.write_text("# c\nSS_TEST_A=from_file\nSS_TEST_B='quoted'\n")
    monkeypatch.setenv("SS_TEST_A", "from_env")
    monkeypatch.delenv("SS_TEST_B", raising=False)
    load_dotenv(f)
    assert os.environ["SS_TEST_A"] == "from_env" and os.environ["SS_TEST_B"] == "quoted"
    monkeypatch.delenv("SS_TEST_B")


def test_manage_url_sandbox_live_and_override():
    assert CFG.subscriber_manage_url == "https://www.sandbox.paypal.com/myaccount/autopay/"
    live = PayPalConfig("a", "b", "https://api-m.paypal.com")
    assert live.subscriber_manage_url == "https://www.paypal.com/myaccount/autopay/"
    assert PayPalConfig("a", "b", manage_url="https://example.test/m").subscriber_manage_url == "https://example.test/m"


# ---------------------------------------------------------------- OAuth + token cache
def test_token_request_uses_basic_auth_and_client_credentials():
    fake = FakePayPal().on("GET", "/v1/x", OK)
    client, _ = make(fake)
    client.get("/v1/x")
    tok = fake.requests[0]
    assert tok.url.path == "/v1/oauth2/token" and tok.method == "POST"
    assert tok.headers["Authorization"] == "Basic " + base64.b64encode(b"CLIENT_ID_123456:SUPER_SECRET_VALUE").decode()
    assert tok.content.decode() == "grant_type=client_credentials"
    assert tok.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
    assert fake.requests[1].headers["Authorization"] == "Bearer tok-1"


def test_token_is_cached_across_calls():
    fake = FakePayPal().on("GET", "/v1/x", OK)
    client, _ = make(fake)
    for _ in range(5):
        client.get("/v1/x")
    assert client.token_requests == 1


def test_token_refreshes_before_it_expires():
    fake = FakePayPal().on("GET", "/v1/x", OK)
    client, clock = make(fake)
    client.get("/v1/x")                       # expires_in 3600 -> refresh point at +3540s
    clock.t += 3539
    client.get("/v1/x")
    assert client.token_requests == 1
    clock.t += 2                               # past the 60s safety margin, still before real expiry
    client.get("/v1/x")
    assert client.token_requests == 2 and fake.requests[-1].headers["Authorization"] == "Bearer tok-2"


def test_scopes_are_remembered_from_the_token_response():
    fake = FakePayPal().on("GET", "/v1/x", OK)
    fake.token_responses = [token_body(scope=f"openid {SUBS_SCOPE}")]
    client, _ = make(fake)
    client.get("/v1/x")
    assert client.scopes == {"openid", SUBS_SCOPE}


def test_bad_client_credentials_give_a_clear_401():
    fake = FakePayPal()
    fake.token_responses = [(401, {"error": "invalid_client", "error_description": "Client Authentication failed"})]
    client, _ = make(fake)
    with pytest.raises(PayPalAuthError, match="didn't accept the client ID and secret") as e:
        client.get("/v1/x")
    assert e.value.status == 401 and e.value.name == "invalid_client" and "SUPER_SECRET_VALUE" not in str(e.value)


def test_api_401_triggers_one_token_refresh_then_succeeds():
    fake = FakePayPal().on("GET", "/v1/x", (401, {"name": "AUTHENTICATION_FAILURE", "message": "expired"}), OK)
    client, _ = make(fake)
    assert client.get("/v1/x") == OK and client.token_requests == 2


def test_persistent_401_raises_auth_error():
    fake = FakePayPal().on("GET", "/v1/x", (401, {"name": "AUTHENTICATION_FAILURE", "message": "no"}))
    client, _ = make(fake)
    with pytest.raises(PayPalAuthError, match="rejected our sign-in"):
        client.get("/v1/x")
    assert client.token_requests == 2          # one refresh attempt, not an endless loop


# ---------------------------------------------------------------- retries
def test_429_is_retried_with_exponential_backoff():
    fake = FakePayPal().on("GET", "/v1/x", (429, {}), (429, {}), (429, {}), OK)
    client, clock = make(fake)
    assert client.get("/v1/x") == OK
    assert clock.sleeps == [0.5, 1.0, 2.0]


def test_5xx_is_retried():
    fake = FakePayPal().on("GET", "/v1/x", (503, {}), (500, {}), OK)
    client, clock = make(fake)
    assert client.get("/v1/x") == OK and clock.sleeps == [0.5, 1.0]


def test_backoff_is_capped():
    fake = FakePayPal().on("GET", "/v1/x", (500, {}))
    client, clock = make(fake, max_retries=6, backoff_cap=3.0)
    with pytest.raises(PayPalUnavailable):
        client.get("/v1/x")
    assert max(clock.sleeps) == 3.0 and len(clock.sleeps) == 6


def test_retry_after_header_is_honoured():
    fake = FakePayPal().on("GET", "/v1/x", (429, {}, {"Retry-After": "7"}), OK)
    client, clock = make(fake)
    client.get("/v1/x")
    assert clock.sleeps == [7.0]


def test_exhausted_429_raises_rate_limited():
    fake = FakePayPal().on("GET", "/v1/x", (429, {}))
    client, clock = make(fake, max_retries=2)
    with pytest.raises(PayPalRateLimited, match="slow down"):
        client.get("/v1/x")
    assert len(clock.sleeps) == 2 and len(fake.api_requests()) == 3


def test_exhausted_5xx_raises_unavailable_with_friendly_text():
    fake = FakePayPal().on("GET", "/v1/x", (502, {}))
    client, _ = make(fake, max_retries=1)
    with pytest.raises(PayPalUnavailable, match="isn't responding"):
        client.get("/v1/x")


def test_network_errors_are_retried_then_succeed():
    req = httpx.Request("GET", "https://x")
    fake = FakePayPal().on("GET", "/v1/x", httpx.ConnectTimeout("t", request=req), OK)
    client, clock = make(fake)
    assert client.get("/v1/x") == OK and clock.sleeps == [0.5]


def test_network_errors_that_persist_raise_unavailable():
    req = httpx.Request("GET", "https://x")
    fake = FakePayPal().on("GET", "/v1/x", httpx.ConnectError("down", request=req))
    client, _ = make(fake, max_retries=2)
    with pytest.raises(PayPalUnavailable):
        client.get("/v1/x")


def test_token_endpoint_is_retried_too():
    fake = FakePayPal().on("GET", "/v1/x", OK)
    fake.token_responses = [(503, {}), token_body()]
    client, clock = make(fake)
    assert client.get("/v1/x") == OK and clock.sleeps == [0.5]


def test_4xx_other_than_429_is_not_retried():
    fake = FakePayPal().on("GET", "/v1/x", (400, {"name": "INVALID_REQUEST", "message": "bad", "debug_id": "dbg1"}))
    client, clock = make(fake)
    with pytest.raises(PayPalError, match="bad") as e:
        client.get("/v1/x")
    assert clock.sleeps == [] and e.value.debug_id == "dbg1" and "dbg1" in e.value.user_message


# ---------------------------------------------------------------- 403 / 404
def test_403_names_the_missing_scope_when_the_token_lacks_it():
    fake = FakePayPal().on("GET", "/v1/x", (403, {"name": "NOT_AUTHORIZED", "message": "Authorization failed due to insufficient permissions."}))
    client, _ = make(fake)
    with pytest.raises(PayPalPermissionError) as e:
        client.get("/v1/x", required_scope=SEARCH_SCOPE)
    assert e.value.missing_scope == SEARCH_SCOPE and SEARCH_SCOPE in e.value.user_message
    assert "Developer Dashboard" in e.value.user_message


def test_403_when_scope_present_gives_generic_permission_message():
    fake = FakePayPal().on("GET", "/v1/x", (403, {"name": "NOT_AUTHORIZED", "details": [{"issue": "PERMISSION_DENIED"}]}))
    fake.token_responses = [token_body(scope=SEARCH_SCOPE)]
    client, _ = make(fake)
    with pytest.raises(PayPalPermissionError) as e:
        client.get("/v1/x", required_scope=SEARCH_SCOPE)
    assert "PERMISSION_DENIED" in e.value.user_message and SEARCH_SCOPE not in e.value.user_message


def test_403_without_a_known_scope_is_still_friendly():
    fake = FakePayPal().on("GET", "/v1/x", (403, {"name": "NOT_AUTHORIZED"}))
    client, _ = make(fake)
    with pytest.raises(PayPalPermissionError, match="isn't permitted"):
        client.get("/v1/x")


def test_404_maps_to_not_found():
    client, _ = make(FakePayPal())
    with pytest.raises(PayPalNotFound, match="couldn't find"):
        client.get("/v1/missing")


def test_empty_204_returns_empty_dict():
    fake = FakePayPal().on("GET", "/v1/x", httpx.Response(204))
    assert make(fake)[0].get("/v1/x") == {}


# ---------------------------------------------------------------- confirmation gate
@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_state_changing_calls_require_confirmation_and_send_nothing(method):
    fake = FakePayPal()
    client, _ = make(fake)
    with pytest.raises(ConfirmationRequired, match="Nothing was sent"):
        client.request(method, "/v1/billing/subscriptions/I-1/cancel", json={"reason": "x"})
    assert fake.requests == []                 # not even a token request


def test_confirmed_state_change_goes_through():
    fake = FakePayPal().on("POST", "/v1/billing/plans", (201, {"id": "P-1"}))
    client, _ = make(fake)
    assert client.request("POST", "/v1/billing/plans", json={}, confirmed=True) == {"id": "P-1"}


def test_reads_never_need_confirmation():
    fake = FakePayPal().on("GET", "/v1/x", OK)
    assert make(fake)[0].get("/v1/x") == OK


# ---------------------------------------------------------------- secrets stay out of logs and errors
def test_no_secret_in_logs_or_error_text(caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakePayPal().on("GET", "/v1/x", (500, {"message": "boom"}))
    client, _ = make(fake, max_retries=1)
    with pytest.raises(PayPalError) as e:
        client.get("/v1/x")
    blob = caplog.text + str(e.value) + repr(client) + repr(e.value.details)
    assert "SUPER_SECRET_VALUE" not in blob and "tok-1" not in blob

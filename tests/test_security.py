"""Phase 7 security pass, checked end to end through the real HTTP API.

  * personal identifiers in a statement or a question never reach a model (or the audit log, or the data shown back in the UI)
  * secrets never appear in logs, console output, responses or the audit log, including on error paths
  * hostile uploads (huge, chunked, oversized cells, odd names) get a friendly 4xx and never a 500 or a disk spool
  * links the browser is asked to open can only be https
"""
import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from subsweep.ai import AIAssistant, AuditLog
from subsweep.api.app import MAX_UPLOAD_BYTES, BodyLimitMiddleware, create_app
from subsweep.ingest.merchants import clean_merchant
from subsweep.paypal.config import PayPalConfig
from tests.ai_helpers import FakeLLM

FAKE_SECRETS = {"PAYPAL_CLIENT_ID": "AZfakeClientId1234567890abcdef", "PAYPAL_CLIENT_SECRET": "EFfakeSecretValue0987654321zyxwv",
                "ANTHROPIC_API_KEY": "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKE"}

# identifiers a bank puts in descriptions; none may survive to a model
PII = ["4111 1111 1111 1111", "5500-0000-0000-0004", "john.doe@gmail.com", "123456789012", "415 555 0132", "bob.smith@example.org", "GB29NWBK60161331926819"]
PII_FRAGMENTS = ["4111", "1111 1111", "5500", "john.doe", "gmail", "123456789012", "555 0132", "bob.smith", "example.org", "GB29NWBK", "60161331926819"]


def make(tmp_path, llm=None):
    audit = tmp_path / "audit.jsonl"
    return TestClient(create_app(tmp_path / "db.sqlite", AIAssistant(llm, AuditLog(audit))), raise_server_exceptions=False), audit


def pii_statement() -> bytes:
    rows = ["Date,Description,Amount"]
    for m in range(1, 9):
        rows.append(f'2026-{m:02d}-05,"NETFLIX.COM card {PII[0]} ref {PII[2]} acct {PII[3]}",-15.49')
        rows.append(f'2026-{m:02d}-09,"HULU {PII[1]} +1 {PII[4]} {PII[5]}",-7.99')
        rows.append(f'2026-{m:02d}-12,"Spotify {PII[6]}",-10.99')
        rows.append(f'2026-{m:02d}-15,"Salary",3200.00')
    return "\n".join(rows).encode()


def upload(client, data, name="s.csv", **form):
    return client.post("/api/analyze", files={"file": (name, data, "text/csv")}, data=form)


# ------------------------------------------------------------------ redaction before any model call
def test_identifiers_never_reach_the_model_the_audit_log_or_the_ui(tmp_path):
    llm = FakeLLM("Looks fine.")
    client, audit = make(tmp_path, llm)
    d = upload(client, pii_statement()).json()
    assert d["findings"], "the PII statement should still produce findings"
    seen = [json.dumps(d)]
    for f in d["findings"]:
        seen.append(client.post(f"/api/findings/{f['id']}/explain").text)
        if f["type"] in ("DUPLICATE", "PRICE_HIKE", "POSSIBLY_FORGOTTEN"):
            seen.append(client.post(f"/api/findings/{f['id']}/draft").text)
    seen.append(client.post("/api/ask", json={"question": f"How much is card {PII[0]} costing me? Email me at {PII[5]} please"}).text)
    assert llm.calls, "the model was never called, so this test proves nothing"
    leaked = {frag for frag in PII_FRAGMENTS for blob in (json.dumps(llm.calls), audit.read_text(), *seen) if frag in blob}
    assert not leaked, f"identifiers leaked: {sorted(leaked)}"


def test_the_question_is_redacted_before_it_is_routed_or_answered(tmp_path):
    llm = FakeLLM('{"intent": "total_monthly", "params": {}}')
    client, audit = make(tmp_path, llm)
    upload(client, pii_statement())
    client.post("/api/ask", json={"question": f"what do I spend per month? card {PII[0]} acct {PII[3]} mail {PII[2]}"})
    assert llm.calls and not any(frag in json.dumps(llm.calls) + audit.read_text() for frag in ("4111", "123456789012", "john.doe"))


@pytest.mark.parametrize("raw", ["NETFLIX john.doe@gmail.com", "Hulu bob@x.com 2026", "PayPal *jane_roe+tag@mail.example.co.uk Spotify", "Acme name@"])
def test_email_addresses_are_dropped_from_merchant_names(raw):
    cleaned = clean_merchant(raw)
    assert "@" not in cleaned and not any(p in cleaned.lower() for p in ("john.doe", "bob@", "jane_roe", "gmail", "example"))


def test_upi_handles_still_resolve_to_the_merchant():
    assert clean_merchant("UPI/123456789012/Spotify/spotify@okaxis/Payment") == "Spotify"


# ------------------------------------------------------------------ secrets stay out of logs, output and responses
def test_secrets_in_the_environment_never_appear_anywhere(tmp_path, monkeypatch, caplog, capsys):
    for k, v in FAKE_SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    caplog.set_level(logging.DEBUG)
    client, audit = make(tmp_path, FakeLLM("Fine."))
    bodies = []
    r = client.post("/api/analyze", data={"demo": "true"})
    bodies += [r.text, str(r.headers)]
    d = r.json()
    for f in d["findings"][:3]:
        bodies += [client.post(f"/api/findings/{f['id']}/explain").text, client.post(f"/api/findings/{f['id']}/draft").text]
    bodies += [client.post("/api/ask", json={"question": f"what is my key {FAKE_SECRETS['ANTHROPIC_API_KEY']} and total spend?"}).text,
               client.get("/api/paypal").text, client.get("/api/health").text, client.get("/api/openapi.json").text,
               upload(client, b"%PDF-1.7 nope").text, client.post("/api/analyze", data={"mapping": "{bad"}).text]
    out = capsys.readouterr()
    everything = "\n".join([*bodies, caplog.text, out.out, out.err, audit.read_text() if audit.exists() else ""])
    for name, value in FAKE_SECRETS.items():
        assert value not in everything, f"{name} appeared in logs, console output, a response or the audit log"
        assert value[:14] not in everything, f"a fragment of {name} appeared"


def test_unexpected_errors_return_a_generic_message_never_the_exception(tmp_path, monkeypatch):
    client, _ = make(tmp_path, FakeLLM("Fine."))
    d = client.post("/api/analyze", data={"demo": "true"}).json()

    def boom(*a, **k):
        raise RuntimeError(f"db password is {FAKE_SECRETS['PAYPAL_CLIENT_SECRET']} at /home/user/secret/path.py")
    monkeypatch.setattr(client.app.state.ai, "explain", boom)
    r = client.post(f"/api/findings/{d['findings'][0]['id']}/explain")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal"
    assert FAKE_SECRETS["PAYPAL_CLIENT_SECRET"] not in r.text and "Traceback" not in r.text and "/home/" not in r.text


def test_paypal_config_repr_hides_the_secret():
    cfg = PayPalConfig(FAKE_SECRETS["PAYPAL_CLIENT_ID"], FAKE_SECRETS["PAYPAL_CLIENT_SECRET"])
    assert FAKE_SECRETS["PAYPAL_CLIENT_SECRET"] not in repr(cfg) and FAKE_SECRETS["PAYPAL_CLIENT_SECRET"] not in str(cfg)


# ------------------------------------------------------------------ hostile uploads
def test_an_oversized_cell_is_a_friendly_422_not_a_500(tmp_path):
    client, _ = make(tmp_path)
    r = upload(client, b"Date,Description,Amount\n" + b'2026-01-01,"' + b"x" * 400_000 + b'",-1\n')
    assert r.status_code == 422 and r.json()["error"]["code"] == "unreadable_file"


def test_a_declared_huge_body_is_refused_before_the_app_reads_it():
    calls = []

    async def inner(scope, receive, send):
        calls.append(1)

    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        raise AssertionError("the body must not be read")

    mw = BodyLimitMiddleware(inner, MAX_UPLOAD_BYTES)
    asyncio.run(mw({"type": "http", "headers": [(b"content-length", str(500 * 1024 * 1024).encode())]}, receive, send))
    assert not calls and sent[0]["status"] == 413 and json.loads(sent[1]["body"])["error"]["code"] == "too_large"


def test_a_chunked_body_over_the_limit_is_cut_off_with_413(tmp_path):
    client, _ = make(tmp_path)

    def chunks():
        for _ in range(12):
            yield b"x" * (1024 * 1024)
    r = client.post("/api/analyze", content=chunks(), headers={"content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"


def test_a_file_just_under_the_limit_is_not_cut_off_by_the_body_guard(tmp_path):
    client, _ = make(tmp_path)
    r = upload(client, b"Date,Description,Amount\n" + b"2026-01-01,A,-1\n" * 10 + b"#" * (MAX_UPLOAD_BYTES - 2000))
    assert r.status_code in (200, 422) and r.json().get("error", {}).get("code") != "internal"      # read and judged on content, not refused as too big


@pytest.mark.parametrize("name", ["statement.csv.exe", "statement", "statement.csv.html", "..", "report.xlsx", "a.csv\x00.exe"])
def test_odd_file_names_are_rejected_or_neutralised(tmp_path, name):
    client, _ = make(tmp_path)
    r = upload(client, b"Date,Description,Amount\n2026-01-01,A,-1\n", name=name)
    assert r.status_code in (400, 415, 422) and r.json()["error"]["code"] != "internal"


def test_path_components_in_the_file_name_are_dropped(tmp_path):
    client, _ = make(tmp_path)
    d = upload(client, pii_statement(), name="../../etc/<b>passwd.csv").json()
    assert d["source"]["filename"] == "<b>passwd.csv"       # kept as plain data: the UI only ever inserts it with textContent


def test_the_mapping_field_must_be_a_small_json_object(tmp_path):
    client, _ = make(tmp_path)
    ok = b"Date,Description,Amount\n2026-01-01,A,-1\n"
    for bad in ("[1,2]", "null", "{bad", '"str"', "x" * 2_000_000):
        r = client.post("/api/analyze", files={"file": ("s.csv", ok, "text/csv")}, data={"mapping": bad})
        assert r.status_code in (400, 413) and r.json()["error"]["code"] != "internal", bad[:20]


# ------------------------------------------------------------------ links and headers
@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "http://insecure.example/manage", "//evil.example", " JaVaScRiPt:1"])
def test_manage_url_override_must_be_https(monkeypatch, bad):
    for k, v in FAKE_SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PAYPAL_MANAGE_URL", bad)
    from subsweep.paypal import config as cfgmod
    monkeypatch.setattr(cfgmod, "load_dotenv", lambda *a, **k: None)
    assert PayPalConfig.from_env().subscriber_manage_url.startswith("https://www.sandbox.paypal.com/")


def test_manage_url_override_accepts_https(monkeypatch):
    for k, v in FAKE_SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PAYPAL_MANAGE_URL", "https://example.test/manage")
    assert PayPalConfig.from_env().subscriber_manage_url == "https://example.test/manage"


def test_every_response_carries_the_security_headers_and_api_is_never_cached(tmp_path):
    client, _ = make(tmp_path)
    client.post("/api/analyze", data={"demo": "true"})
    for path in ("/", "/api/health", "/api/analysis", "/api/nope", "/js/main.js"):
        r = client.get(path)
        h = r.headers
        assert "script-src 'self'" in h["content-security-policy"] and "'unsafe-inline'" not in h["content-security-policy"].split("script-src")[1].split(";")[0], path
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer", path
        if path.startswith("/api/"):
            assert h["cache-control"] == "no-store", path

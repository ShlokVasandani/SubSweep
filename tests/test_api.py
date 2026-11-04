"""API tests: upload limits and file checks, sessions, decisions (dismiss / still using / restore), AI endpoints, headers."""
import io
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from subsweep.ai import AIAssistant, AuditLog
from subsweep.api import app as app_module
from subsweep.api.app import COOKIE, create_app
from subsweep.demo import demo_statement_csv
from tests.ai_helpers import FakeLLM

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("PAYPAL_MODE", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "ANTHROPIC_API_KEY", "SUBSWEEP_AI"):
        monkeypatch.delenv(k, raising=False)


def make(tmp_path, llm=None, db="db.sqlite"):
    ai = AIAssistant(llm, AuditLog(tmp_path / "audit.jsonl"))
    return TestClient(create_app(tmp_path / db, ai))


@pytest.fixture
def client(tmp_path):
    return make(tmp_path)


@pytest.fixture
def demo(client):
    r = client.post("/api/analyze", data={"demo": "true"})
    assert r.status_code == 200
    return client, r.json()


def upload(client, content, name="statement.csv", **data):
    return client.post("/api/analyze", files={"file": (name, content if isinstance(content, bytes) else content.encode(), "text/csv")}, data=data)


def ftype(d, t):
    return [f for f in d["findings"] if f["type"] == t]


# ---------------------------------------------------------------- basics and security headers
def test_health_reports_ai_and_paypal_mode(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["ai"] == {"mode": "offline", "model": None} and h["limits"]["max_upload_mb"] == 5 and h["paypal_mode"] == "live"


def test_health_reports_the_model_when_one_is_configured(tmp_path):
    h = make(tmp_path, FakeLLM("x", model="claude-test")).get("/api/health").json()
    assert h["ai"] == {"mode": "model", "model": "claude-test"}


def test_security_headers_on_api_and_pages(client):
    for path in ("/api/health", "/"):
        r = client.get(path)
        assert "script-src 'self'" in r.headers["content-security-policy"] and "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "no-referrer"
    assert client.get("/api/health").headers["cache-control"] == "no-store"


def test_csp_forbids_external_sources(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in csp and "http" not in csp and "unsafe-eval" not in csp and "script-src 'self'" in csp


def test_frontend_and_vendored_assets_are_served(client):
    assert "SubSweep" in client.get("/").text
    assert client.get("/vendor/chart.umd.js").status_code == 200 and client.get("/css/tokens.css").status_code == 200
    assert client.get("/vendor/fonts/fraunces-latin-opsz-normal.woff2").status_code == 200


def test_unknown_routes_and_bad_requests_use_the_friendly_error_shape(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.post("/api/ask", json={"question": ""})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request" and "detail" not in r.json()


# ---------------------------------------------------------------- analyse: demo and uploads
def test_demo_analysis_has_everything_the_ui_needs(demo):
    _, d = demo
    assert set(d) == {"id", "created", "source", "summary", "findings", "subscriptions", "excluded", "categories", "paypal"}
    assert d["source"]["kind"] == "demo" and d["source"]["rows"] == 588 and d["summary"]["health"]["score"] == 45
    assert len(d["subscriptions"]) == 10 and d["summary"]["active_subscriptions"] == 10
    assert d["paypal"]["mode"] == "mock" and len(d["paypal"]["rows"]) == 4 and d["paypal"]["sources"]["confirmed_by_paypal"] == 17


def test_demo_findings_match_what_the_generator_planted(demo):
    _, d = demo
    assert {f["type"] for f in d["findings"]} == {"DUPLICATE", "PRICE_HIKE", "POSSIBLY_FORGOTTEN", "ANNUAL_RENEWAL_SOON", "PAYPAL_MISMATCH"}
    assert ftype(d, "DUPLICATE")[0]["merchant"] == "Streamly" and ftype(d, "PRICE_HIKE")[0]["merchant"] == "Netflix"
    assert {f["merchant"] for f in ftype(d, "POSSIBLY_FORGOTTEN")} >= {"Hulu", "FitPass Gym"}
    assert ftype(d, "ANNUAL_RENEWAL_SOON")[0]["merchant"] == "Namecheap" and ftype(d, "PAYPAL_MISMATCH")[0]["merchant"] == "FitPass Gym"
    names = {s["merchant"] for s in d["subscriptions"]}
    assert not names & {"Kroger Cincinnati", "Shell Oil", "Starbucks Store", "Uber Trip"}              # groceries / fuel / coffee never flagged
    assert {e["rule"] for e in d["excluded"]} >= {"category:groceries", "category:fuel"}


def test_cookie_is_httponly_and_samesite_strict(client):
    c = client.post("/api/analyze", data={"demo": "true"}).headers["set-cookie"].lower()
    assert COOKIE in c and "httponly" in c and "samesite=strict" in c


def test_upload_of_a_real_statement_file(client):
    r = upload(client, (SAMPLES / "demo_statement.csv").read_bytes(), "my bank.csv")
    d = r.json()
    assert r.status_code == 200 and d["source"] == {**d["source"], "kind": "upload", "filename": "my bank.csv", "format": "generic_bank"}
    assert d["paypal"]["mode"] == "off" and "PAYPAL_MODE=mock" in d["paypal"]["notice"]      # no PayPal configured for uploads


def test_paypal_demo_data_is_used_for_uploads_when_mode_is_mock(client, monkeypatch):
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    d = upload(client, demo_statement_csv()).json()
    assert d["paypal"]["mode"] == "mock" and ftype(d, "PAYPAL_MISMATCH")


def test_upload_can_opt_out_of_paypal(client, monkeypatch):
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    d = upload(client, demo_statement_csv(), use_paypal="false").json()
    assert d["paypal"]["mode"] == "off" and not ftype(d, "PAYPAL_MISMATCH")


@pytest.mark.parametrize("name", ["statement.pdf", "statement.xlsx", "statement", "statement.csv.exe", "a.json"])
def test_wrong_file_types_are_rejected_politely(client, name):
    r = upload(client, "Date,Description,Amount\n2026-01-01,A,-1\n", name)
    assert r.status_code == 415 and r.json()["error"]["code"] == "wrong_file_type" and ".csv" in r.json()["error"]["message"]


@pytest.mark.parametrize("magic", [b"%PDF-1.7 ...", b"PK\x03\x04zip", b"\x89PNG\r\n", b"\xff\xd8\xff\xe0", b"MZ\x90\x00", b"\x7fELF"])
def test_binary_files_renamed_to_csv_are_rejected(client, magic):
    r = upload(client, magic + b"\x00" * 50, "statement.csv")
    assert r.status_code == 415 and "binary" in r.json()["error"]["message"]


def test_nul_bytes_are_rejected(client):
    assert upload(client, b"Date,Description\n2026-01-01,A\x00B\n").status_code == 415


def test_oversized_uploads_are_refused(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 500)
    r = upload(client, "Date,Description,Amount\n" + "2026-01-01,Shop,-1.00\n" * 100)
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large" and "shorter date range" in r.json()["error"]["message"]


def test_empty_and_missing_files(client):
    assert upload(client, "   \n").status_code == 400
    r = client.post("/api/analyze", data={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_file"


def test_bad_currency_code(client):
    assert upload(client, demo_statement_csv(), default_currency="DOLLARS").status_code == 400
    assert upload(client, "Date,Description,Amount\n2026-01-01,A,-1\n", default_currency="EUR").status_code == 200


def test_unreadable_columns_ask_for_a_mapping_with_headers_and_guesses(client):
    r = upload(client, "When,What,HowMuch\n2026-01-01,NETFLIX,-15.49\n")
    e = r.json()["error"]
    assert r.status_code == 422 and e["code"] == "needs_mapping" and e["details"]["headers"] == ["When", "What", "HowMuch"] and "Pick them below" in e["message"]


def test_manual_mapping_completes_the_analysis(client):
    csv_text = "When,What,HowMuch\n" + "".join(f"2026-{m:02d}-05,NETFLIX.COM,-15.49\n" for m in range(1, 7))
    r = upload(client, csv_text, mapping=json.dumps({"date": "When", "description": "What", "amount": "HowMuch"}))
    d = r.json()
    assert r.status_code == 200 and d["source"]["format"] == "manual" and d["subscriptions"][0]["merchant"] == "Netflix"


@pytest.mark.parametrize("bad", ["not json", "[1,2]", '"x"'])
def test_garbled_mapping_is_a_friendly_error(client, bad):
    r = upload(client, "When,What,HowMuch\n2026-01-01,A,-1\n", mapping=bad)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_mapping"


def test_invert_sign_for_exports_that_list_purchases_as_positive(client):
    text = "Date,Description,Amount\n" + "".join(f"2026-{m:02d}-05,NETFLIX.COM,15.49\n" for m in range(1, 7))
    assert upload(client, text).json()["subscriptions"] == []                      # positives are credits: nothing to find
    d = upload(client, text, invert_sign="true").json()
    assert d["subscriptions"][0]["merchant"] == "Netflix" and d["source"]["rows"] == 6


def test_a_file_with_no_transactions(client):
    r = upload(client, "Date,Description,Amount\nnot-a-date,A,oops\n")
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_transactions"


def test_a_short_statement_is_a_valid_empty_result(client):
    d = upload(client, "Date,Description,Amount\n2026-01-01,Coffee,-3\n2026-01-02,Lunch,-9\n").json()
    assert d["subscriptions"] == [] and d["findings"] == [] and d["summary"]["health"]["score"] == 100


def test_paypal_activity_csv_is_understood(client):
    d = upload(client, (SAMPLES / "paypal_activity.csv").read_bytes(), "paypal.csv").json()
    assert d["source"]["format"] == "paypal_activity"


# ---------------------------------------------------------------- sessions
def test_no_analysis_yet_is_a_404_with_friendly_copy(client):
    for path in ("/api/analysis", "/api/paypal", "/api/subscriptions/x/history"):
        r = client.get(path)
        assert r.status_code == 404 and r.json()["error"]["code"] == "no_analysis" and "Upload one" in r.json()["error"]["message"]


def test_analysis_survives_a_reload_via_the_cookie(demo):
    client, d = demo
    assert client.get("/api/analysis").json()["id"] == d["id"]


def test_sessions_are_isolated(demo, tmp_path):
    _, d = demo
    other = TestClient(client_app := demo[0].app)
    assert other.get("/api/analysis").status_code == 404


def test_forget_removes_the_analysis_and_cookie(demo):
    client, _ = demo
    assert client.delete("/api/analysis").status_code == 200
    assert client.get("/api/analysis").status_code == 404


def test_old_sessions_are_evicted(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_SESSIONS", 2)
    app = create_app(tmp_path / "db.sqlite", AIAssistant(None, AuditLog(tmp_path / "a.jsonl")))
    clients = [TestClient(app) for _ in range(3)]
    for c in clients:
        assert c.post("/api/analyze", data={"demo": "true"}).status_code == 200
    assert clients[0].get("/api/analysis").status_code == 404 and clients[2].get("/api/analysis").status_code == 200


# ---------------------------------------------------------------- decisions
def test_dismiss_removes_it_from_open_totals_and_undo_restores(demo):
    client, d = demo
    f = ftype(d, "DUPLICATE")[0]           # removes real savings, so the score must move (the severity term is capped at 30)
    before = d["summary"]
    r = client.post(f"/api/findings/{f['id']}/dismiss", json={}).json()
    got = next(x for x in r["findings"] if x["id"] == f["id"])
    assert got["status"] == "dismissed" and got["dismissed_reason"] == "dismissed"
    assert r["summary"]["open_findings"] == before["open_findings"] - 1 and r["summary"]["health"]["score"] > before["health"]["score"]
    back = client.post(f"/api/findings/{f['id']}/restore").json()
    assert next(x for x in back["findings"] if x["id"] == f["id"])["status"] == "open" and back["summary"]["health"]["score"] == before["health"]["score"]


def test_still_using_dismisses_a_forgotten_finding_and_remembers_why(demo):
    client, d = demo
    f = next(x for x in ftype(d, "POSSIBLY_FORGOTTEN") if x["merchant"] == "Hulu")
    assert f["can_still_use"] and f["is_guess"]
    r = client.post(f"/api/findings/{f['id']}/dismiss", json={"reason": "still_using"}).json()
    assert next(x for x in r["findings"] if x["id"] == f["id"])["dismissed_reason"] == "still_using"
    assert r["summary"]["potential_savings_annual"] < d["summary"]["potential_savings_annual"]


def test_still_using_only_applies_to_forgotten_findings(demo):
    client, d = demo
    r = client.post(f"/api/findings/{ftype(d, 'DUPLICATE')[0]['id']}/dismiss", json={"reason": "still_using"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_applicable"


def test_bad_reasons_and_unknown_findings(demo):
    client, _ = demo
    assert client.post("/api/findings/fnd_x/dismiss", json={}).status_code == 404
    assert client.post("/api/findings/fnd_x/dismiss", json={"reason": "because"}).status_code == 422
    assert client.post("/api/findings/fnd_x/restore").status_code == 404


def test_decisions_persist_across_restarts_and_re_uploads(tmp_path):
    a = make(tmp_path)
    d = a.post("/api/analyze", data={"demo": "true"}).json()
    fid = ftype(d, "PRICE_HIKE")[0]["id"]
    a.post(f"/api/findings/{fid}/dismiss", json={})
    b = make(tmp_path)                                                    # a fresh process, same database file
    d2 = b.post("/api/analyze", data={"demo": "true"}).json()
    assert next(f for f in d2["findings"] if f["id"] == fid)["status"] == "dismissed"


def test_reset_all_decisions(demo):
    client, d = demo
    for f in d["findings"][:3]:
        client.post(f"/api/findings/{f['id']}/dismiss", json={})
    r = client.delete("/api/dismissals").json()
    assert all(f["status"] == "open" for f in r["findings"])


# ---------------------------------------------------------------- AI endpoints
def test_explain_without_a_model_returns_the_built_in_text_and_the_data_used(demo):
    client, d = demo
    r = client.post(f"/api/findings/{ftype(d, 'PRICE_HIKE')[0]['id']}/explain").json()
    assert r["generated_by"] == "template" and "$17.99" in r["text"] and r["data_used"]["finding_type"] == "PRICE_HIKE" and r["notes"]


def test_explain_with_a_model_is_grounded_and_labelled(tmp_path):
    text = "Netflix went from $15.49 to $17.99 per month, which adds about $30.00 a year."
    client = make(tmp_path, FakeLLM(text))
    d = client.post("/api/analyze", data={"demo": "true"}).json()
    r = client.post(f"/api/findings/{ftype(d, 'PRICE_HIKE')[0]['id']}/explain").json()
    assert r["generated_by"] == "model" and r["text"] == text and r["model"] == "fake-model"


def test_a_model_that_invents_numbers_is_replaced_by_the_template(tmp_path):
    client = make(tmp_path, FakeLLM("Netflix is now $99.00 a month."))
    d = client.post("/api/analyze", data={"demo": "true"}).json()
    r = client.post(f"/api/findings/{ftype(d, 'PRICE_HIKE')[0]['id']}/explain").json()
    assert r["generated_by"] == "template" and r["status"] == "grounding_violation" and "$99.00" not in r["text"]


def test_draft_is_returned_with_placeholders_and_never_sent(demo):
    client, d = demo
    r = client.post(f"/api/findings/{ftype(d, 'DUPLICATE')[0]['id']}/draft").json()
    assert r["sent"] is False and "[ACCOUNT NAME]" in r["body"] and "[ORDER / TRANSACTION DETAILS]" in r["body"] and "[YOUR NAME]" in r["body"] and r["subject"]


def test_only_some_findings_can_be_drafted(demo):
    client, d = demo
    r = client.post(f"/api/findings/{ftype(d, 'ANNUAL_RENEWAL_SOON')[0]['id']}/draft")
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_draftable"
    flags = {f["type"]: f["can_draft"] for f in d["findings"]}
    assert flags["DUPLICATE"] and flags["PRICE_HIKE"] and flags["POSSIBLY_FORGOTTEN"] and not flags["ANNUAL_RENEWAL_SOON"] and not flags["PAYPAL_MISMATCH"]


def test_ask_answers_from_computed_data(demo):
    client, _ = demo
    r = client.post("/api/ask", json={"question": "Which subscription went up the most this year?"}).json()
    assert r["status"] == "answered" and r["intent"] == "largest_price_increase" and "Netflix" in r["answer"] and r["table"][0]["subscription"] == "Netflix"
    assert r["data_used"]["analysis"]["rows"]


def test_ask_refuses_investment_advice_and_off_topic(demo):
    client, _ = demo
    inv = client.post("/api/ask", json={"question": "Which stocks should I buy?"}).json()
    off = client.post("/api/ask", json={"question": "write me a poem about cats"}).json()
    assert inv["status"] == "refused" and "investment advice" in inv["answer"] and off["status"] == "refused" and inv["data_used"] == {}


def test_ask_says_so_when_there_is_no_data(demo):
    client, _ = demo
    r = client.post("/api/ask", json={"question": "What do I spend on gaming?"}).json()
    assert r["status"] == "no_data" and "can't find anything" in r["answer"]


def test_question_length_limit(demo):
    client, _ = demo
    assert client.post("/api/ask", json={"question": "x" * 501}).status_code == 422
    assert client.post("/api/ask", json={"question": "what is my total per month"}).status_code == 200


def test_ai_use_is_capped_per_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_AI_CALLS_PER_ANALYSIS", 2)
    client = make(tmp_path)
    client.post("/api/analyze", data={"demo": "true"})
    assert client.post("/api/ask", json={"question": "total per month"}).status_code == 200
    assert client.post("/api/ask", json={"question": "total per month"}).status_code == 200
    r = client.post("/api/ask", json={"question": "total per month"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "ai_limit"


def test_every_ai_call_lands_in_the_audit_log(tmp_path):
    client = make(tmp_path)
    d = client.post("/api/analyze", data={"demo": "true"}).json()
    client.post(f"/api/findings/{d['findings'][0]['id']}/explain")
    client.post("/api/ask", json={"question": "which stocks should I buy"})
    rows = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [r["feature"] for r in rows] == ["explain", "qa"] and rows[1]["status"].startswith("blocked")


# ---------------------------------------------------------------- history and PayPal views
def test_charge_history_for_the_chart(demo):
    client, d = demo
    nf = next(s for s in d["subscriptions"] if s["merchant"] == "Netflix")
    h = client.get(f"/api/subscriptions/{nf['id']}/history").json()
    assert len(h["points"]) == 24 and h["points"][0]["amount"] == 15.49 and h["points"][-1]["amount"] == 17.99
    assert h["price_changes"][0]["kind"] == "increase" and h["points"][0]["date"] < h["points"][-1]["date"]
    assert client.get("/api/subscriptions/sub_nope/history").status_code == 404


def test_paypal_charges_in_history_are_marked_confirmed(demo):
    client, d = demo
    fp = next(s for s in d["subscriptions"] if s["merchant"] == "FitPass Gym")
    pts = client.get(f"/api/subscriptions/{fp['id']}/history").json()["points"]
    assert sum(p["confirmed_by_paypal"] for p in pts) == 5 and fp["paypal"]["status"] == "CANCELLED"


def test_paypal_endpoint_and_manage_links(demo):
    client, d = demo
    p = client.get("/api/paypal").json()
    assert p["mode"] == "mock" and all(r["manage_url"].endswith("/myaccount/autopay/") for r in p["rows"])
    assert sorted(r["status"] for r in p["rows"]) == ["ACTIVE", "ACTIVE", "ACTIVE", "CANCELLED"]


def test_user_supplied_text_is_returned_as_data_not_markup(client):
    text = "Date,Description,Amount\n" + "".join(f"2026-{m:02d}-05,<img src=x onerror=alert(1)> CLOUD,-9.99\n" for m in range(1, 7))
    d = upload(client, text, "<img src=x onerror=alert(1)>.csv").json()
    assert d["source"]["filename"] == "<img src=x onerror=alert(1)>.csv"        # raw data; the UI inserts it with textContent
    assert "onerror" in d["subscriptions"][0]["merchant"]                   # hostile text survives as plain data


def test_filenames_are_trimmed_and_paths_stripped(client):
    d = upload(client, demo_statement_csv(), "../../etc/" + "a" * 200 + ".csv").json()
    assert "/" not in d["source"]["filename"] and len(d["source"]["filename"]) <= 120

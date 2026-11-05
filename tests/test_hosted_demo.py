"""The hosted (serverless) build: create_app(hosted=True).

A serverless host may answer every request from a different instance and keeps no state for a visitor, so the browser re-sends its statement
with each action. These tests build SEVERAL separate apps and move a visitor between them, which is exactly what the host does.
"""
import base64
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from subsweep.ai import AIAssistant, AuditLog
from subsweep.api import app as app_module
from subsweep.api.app import HOSTED_MAX_UPLOAD_BYTES, HOSTED_MESSAGE, create_app
from subsweep.api.decisions import COOKIE as DECISIONS_COOKIE
from subsweep.api.decisions import MAX_DECISIONS, parse_cookie
from subsweep.demo import demo_statement_csv

ROOT = Path(__file__).resolve().parent.parent
STATEMENT = demo_statement_csv().encode()
HEADERS = {"X-SubSweep-Stateless": "1"}


def instance(tmp_path, name="a", **kw):
    app = create_app(":memory:", AIAssistant(None, AuditLog(tmp_path / f"{name}.jsonl")), hosted=True, **kw)
    return TestClient(app, raise_server_exceptions=False, base_url="https://demo.test")


def send(client, method, path, *, statement=None, demo=False, options=None, body=None, filename="statement.csv", headers=None):
    """What the browser does for every action: a multipart POST carrying the statement, its options and the original JSON body."""
    opts = {"filename": filename, **(options or {}), **({"demo": True} if demo else {})}
    parts = {"options": (None, json.dumps(opts))}
    if statement is not None:
        parts["statement"] = (filename, statement, "text/csv")
    if body is not None:
        parts["json"] = (None, json.dumps(body))
    return client.post(path, files=parts, headers={**HEADERS, "X-SubSweep-Method": method, **(headers or {})})


def upload(client, data=STATEMENT, name="statement.csv", **form):
    return client.post("/api/analyze", files={"file": (name, data, "text/csv")}, data=form)


def dup_id(overview):
    return next(f["id"] for f in overview["findings"] if f["type"] == "DUPLICATE")


def encode(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


# ---------------------------------------------------------------- the cookie that holds a visitor's decisions
@pytest.mark.parametrize("raw", [None, "", "###", "not base64!", encode([1, 2]), encode("str"), encode(None), "A" * 5000])
def test_a_malformed_decisions_cookie_is_ignored_never_an_error(raw):
    assert parse_cookie(raw) == {}


def test_only_known_finding_ids_and_reasons_survive():
    good = {"fnd_1234abcd": "dismissed", "fnd_0123456789abcdef": "still_using"}
    bad = {"nope": "dismissed", "fnd_1234abcd5": "deleted", "../../etc": "dismissed", "fnd_XYZ": "dismissed", "fnd_12": "dismissed"}
    assert parse_cookie(encode({**good, **bad})) == good


def test_a_tampered_cookie_cannot_grow_without_bound():
    assert len(parse_cookie(encode({f"fnd_{i:08x}": "dismissed" for i in range(500)}))) <= MAX_DECISIONS


# ---------------------------------------------------------------- uploads work, with no memory shared between requests
def test_health_describes_the_hosted_build(tmp_path):
    h = instance(tmp_path).get("/api/health").json()
    assert h["hosted"] is True and h["uploads"] is True and h["limits"]["max_upload_mb"] == 4


def test_a_real_statement_can_be_uploaded_and_analysed(tmp_path):
    c = instance(tmp_path)
    r = upload(c)
    d = r.json()
    assert r.status_code == 200 and d["source"]["kind"] == "upload" and d["source"]["filename"] == "statement.csv"
    assert d["summary"]["active_subscriptions"] == 10
    assert "set-cookie" not in r.headers or "subsweep_session" not in r.headers["set-cookie"]      # no server-side session exists


def test_an_uploaded_statement_never_gets_the_fictional_paypal_demo_data(tmp_path):
    d = upload(instance(tmp_path)).json()                                   # the same rows as the sample, but uploaded
    assert d["paypal"]["mode"] == "off"
    assert not any(f["type"] == "PAYPAL_MISMATCH" for f in d["findings"]) and len(d["findings"]) == 6


def test_every_action_works_on_an_instance_that_has_never_seen_the_visitor(tmp_path):
    first = instance(tmp_path, "a")
    d = upload(first).json()
    fid, sub_id = dup_id(d), d["subscriptions"][0]["id"]
    cold = instance(tmp_path, "b")                                           # a different instance, empty memory
    r = send(cold, "GET", "/api/analysis", statement=STATEMENT)
    assert r.status_code == 200 and r.json()["summary"] == d["summary"]
    assert send(cold, "GET", f"/api/subscriptions/{sub_id}/history", statement=STATEMENT).status_code == 200
    assert send(cold, "GET", "/api/paypal", statement=STATEMENT).json()["mode"] == "off"
    assert send(cold, "POST", f"/api/findings/{fid}/explain", statement=STATEMENT).status_code == 200
    assert send(cold, "POST", f"/api/findings/{fid}/draft", statement=STATEMENT).json()["sent"] is False
    answer = send(cold, "POST", "/api/ask", statement=STATEMENT, body={"question": "What do I spend on streaming?"})
    assert answer.status_code == 200 and "$" in answer.json()["answer"]


def test_decisions_follow_the_visitor_across_instances_but_never_leak_to_others(tmp_path):
    alice, bob = instance(tmp_path, "a"), instance(tmp_path, "b")
    fid = dup_id(upload(alice).json())
    status = lambda client: next(f["status"] for f in send(client, "GET", "/api/analysis", statement=STATEMENT).json()["findings"] if f["id"] == fid)

    r = send(alice, "POST", f"/api/findings/{fid}/dismiss", statement=STATEMENT, body={"reason": "dismissed"})
    assert r.status_code == 200 and DECISIONS_COOKIE in r.headers["set-cookie"]
    assert status(alice) == "dismissed"
    assert status(bob) == "open"                                              # another visitor, same statement: unaffected
    carried = instance(tmp_path, "c")
    carried.cookies.update(alice.cookies)
    assert status(carried) == "dismissed"                                     # Alice's decision survives a new instance

    send(alice, "POST", f"/api/findings/{fid}/restore", statement=STATEMENT)
    assert status(alice) == "open"
    send(alice, "POST", f"/api/findings/{fid}/dismiss", statement=STATEMENT, body={"reason": "dismissed"})
    send(alice, "DELETE", "/api/dismissals", statement=STATEMENT)
    assert status(alice) == "open"


def test_two_different_statements_never_mix(tmp_path):
    c = instance(tmp_path)
    tiny = ("Date,Description,Amount\n" + "".join(f"2026-{m:02d}-05,Gymbox Club,-30.00\n" for m in range(1, 9))).encode()
    a = send(c, "GET", "/api/analysis", statement=STATEMENT).json()
    b = send(c, "GET", "/api/analysis", statement=tiny, filename="gym.csv").json()
    assert a["summary"]["active_subscriptions"] == 10 and b["summary"]["active_subscriptions"] == 1
    assert b["source"]["filename"] == "gym.csv" and {s["merchant"] for s in b["subscriptions"]} == {"Gymbox Club"}


def test_the_sample_statement_needs_no_upload_and_is_labelled_demo_data(tmp_path):
    c = instance(tmp_path)
    d = send(c, "GET", "/api/analysis", demo=True).json()
    assert d["source"]["kind"] == "demo" and d["paypal"]["mode"] == "mock" and len(d["findings"]) == 7
    assert c.post("/api/analyze", data={"demo": "true"}).json()["summary"] == d["summary"]


def test_a_warm_instance_reuses_the_analysis_and_a_changed_statement_rebuilds(tmp_path, monkeypatch):
    calls = []
    real = app_module.analysis_from_text
    monkeypatch.setattr(app_module, "analysis_from_text", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    c = instance(tmp_path)
    for _ in range(3):
        assert send(c, "GET", "/api/analysis", statement=STATEMENT).status_code == 200
    assert len(calls) == 1
    send(c, "GET", "/api/analysis", statement=STATEMENT, filename="other.csv")
    assert len(calls) == 2


def test_two_visitors_views_are_independent_objects_even_when_alive_at_once(tmp_path):
    """Concurrent requests share an instance. A shared view would let one visitor's decisions change what another is shown mid-request."""
    class Decisions:
        def __init__(self, d):
            self._d = d

        def dismissals(self):
            return dict(self._d)

    c = instance(tmp_path)
    fid = dup_id(upload(c).json())
    view, ss = c.app.state.hosted_view, {"demo": False, "data": STATEMENT, "options": app_module.parse_options(json.dumps({"filename": "statement.csv"}))}
    alice, bob = view(ss, Decisions({fid: "dismissed"})), view(ss, Decisions({}))
    assert alice is not bob and alice.report is not bob.report
    status = lambda a: next(f.status for f in a.report.findings if f.id == fid)
    assert status(alice) == "dismissed" and status(bob) == "open"


def test_still_using_keeps_its_rule(tmp_path):
    c = instance(tmp_path)
    fid = dup_id(upload(c).json())
    r = send(c, "POST", f"/api/findings/{fid}/dismiss", statement=STATEMENT, body={"reason": "still_using"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_applicable"


# ---------------------------------------------------------------- the same friendly errors, and strict validation
def test_upload_errors_are_the_familiar_friendly_ones(tmp_path):
    c = instance(tmp_path)
    assert upload(c, b"%PDF-1.7 nope").status_code == 415
    assert upload(c, b"   \n").status_code == 400
    assert upload(c, b"Date,Description,Amount\n", name="notes.exe").status_code == 415
    odd = upload(c, ("When,What,HowMuch\n" + "".join(f"2026-{m:02d}-05,NETFLIX.COM,-15.49\n" for m in range(1, 7))).encode())
    assert odd.status_code == 422 and odd.json()["error"]["code"] == "needs_mapping" and "headers" in odd.json()["error"]["details"]


def test_a_statement_over_the_hosted_limit_is_refused_politely(tmp_path):
    c = instance(tmp_path)
    r = upload(c, b"Date,Description,Amount\n" + b"2026-01-01,A,-1\n" * 300_000)           # about 4.8 MB
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large" and "4 MB" in r.json()["error"]["message"]
    r = send(c, "GET", "/api/analysis", statement=b"x" * (HOSTED_MAX_UPLOAD_BYTES + 1000))
    assert r.status_code == 413


def test_a_just_under_limit_statement_is_judged_on_content_not_refused_for_size(tmp_path):
    r = upload(instance(tmp_path), b"Date,Description,Amount\n" + b"2026-01-01,A,-1\n" * 10 + b"#" * (HOSTED_MAX_UPLOAD_BYTES - 5000))
    assert r.status_code in (200, 422) and r.json().get("error", {}).get("code") != "internal"


@pytest.mark.parametrize("options", ["{bad", "[1]", '"x"', json.dumps({"mapping": [1]}), json.dumps({"default_currency": "EURO"}), json.dumps({"default_currency": 5})])
def test_bad_options_are_a_400_never_a_500(tmp_path, options):
    c = instance(tmp_path)
    r = c.post("/api/analysis", files={"options": (None, options), "statement": ("s.csv", STATEMENT, "text/csv")},
               headers={**HEADERS, "X-SubSweep-Method": "GET"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


def test_only_get_post_and_delete_can_be_requested(tmp_path):
    c = instance(tmp_path)
    for method in ("PUT", "PATCH", "TRACE", "x"):
        assert send(c, "GET", "/api/analysis", statement=STATEMENT, headers={"X-SubSweep-Method": method}).status_code == 400


def test_without_a_statement_nothing_is_loaded(tmp_path):
    c = instance(tmp_path)
    assert c.get("/api/analysis").status_code == 404
    assert send(c, "GET", "/api/analysis").status_code == 404
    assert send(c, "POST", "/api/ask", body={"question": "hi"}).status_code == 404


def test_the_stateless_transport_is_only_honoured_on_api_routes(tmp_path):
    c = instance(tmp_path)
    r = c.post("/", files={"options": (None, "{}")}, headers=HEADERS)
    assert r.status_code in (404, 405)
    assert c.post("/api/analyze", files={"file": ("s.csv", b"Date,Description,Amount\n2026-01-01,A,-1\n", "text/csv")}, headers=HEADERS).status_code in (200, 422)


def test_the_hosted_build_writes_nothing_to_the_working_directory(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    c = instance(tmp_path)
    d = upload(c).json()
    send(c, "POST", f"/api/findings/{dup_id(d)}/explain", statement=STATEMENT)
    send(c, "POST", "/api/ask", statement=STATEMENT, body={"question": "How much could I save?"})
    send(c, "DELETE", "/api/analysis", statement=STATEMENT)
    assert list(work.iterdir()) == []                                          # no data/, no logs/: a serverless disk is read-only


def test_the_security_headers_are_still_on_every_response(tmp_path):
    r = send(instance(tmp_path), "GET", "/api/analysis", statement=STATEMENT)
    assert "script-src 'self'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"


# ---------------------------------------------------------------- the sample-only option
def test_sample_only_mode_refuses_uploads_and_still_serves_the_sample(tmp_path):
    c = instance(tmp_path, uploads=False)
    assert c.get("/api/health").json()["uploads"] is False
    r = upload(c)
    assert r.status_code in (403, 413) and r.json()["error"]["code"] == "hosted_demo" and r.json()["error"]["message"] == HOSTED_MESSAGE
    assert send(c, "GET", "/api/analysis", demo=True).status_code == 200


# ---------------------------------------------------------------- the serverless entrypoint
@pytest.mark.parametrize("uploads_env,expected", [(None, True), ("on", True), ("off", False)])
def test_the_root_entrypoint_builds_the_hosted_app(monkeypatch, uploads_env, expected):
    monkeypatch.setenv("PAYPAL_MODE", "live")                                  # a hosted build must override this
    monkeypatch.delenv("SUBSWEEP_HOSTED_UPLOADS", raising=False)
    if uploads_env:
        monkeypatch.setenv("SUBSWEEP_HOSTED_UPLOADS", uploads_env)
    spec = importlib.util.spec_from_file_location("vercel_entry", ROOT / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    h = TestClient(mod.app).get("/api/health").json()
    assert h["hosted"] is True and h["uploads"] is expected and h["paypal_mode"] == "mock" and h["ai"]["mode"] == "offline"

"""The hosted demo build (create_app(hosted_demo=True)): sample data only, no disk, no memory shared between requests.

A serverless host may answer every request from a different instance, so these tests build SEVERAL separate apps and move a visitor's
cookies between them, which is exactly what the host does.
"""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from subsweep.ai import AIAssistant, AuditLog
from subsweep.api.app import HOSTED_MESSAGE, create_app
from subsweep.api.decisions import COOKIE as DECISIONS_COOKIE
from subsweep.api.decisions import MAX_DECISIONS, parse_cookie


def instance(tmp_path, name="a"):
    app = create_app(":memory:", AIAssistant(None, AuditLog(tmp_path / f"{name}.jsonl")), hosted_demo=True)
    return TestClient(app, raise_server_exceptions=False, base_url="https://demo.test")


def visit(client, other):
    """A visitor's cookies, carried over to another server instance."""
    other.cookies.update(client.cookies)
    return other


def encode(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


# ---------------------------------------------------------------- the cookie that holds a visitor's decisions
@pytest.mark.parametrize("raw", [None, "", "###", "not base64!", encode([1, 2]), encode("str"), encode(None), "A" * 5000, "e30"[:2]])
def test_a_malformed_decisions_cookie_is_ignored_never_an_error(raw):
    assert parse_cookie(raw) == {} or isinstance(parse_cookie(raw), dict)
    assert parse_cookie(raw) == {}


def test_only_known_finding_ids_and_reasons_survive():
    good = {"fnd_1234abcd": "dismissed", "fnd_0123456789abcdef": "still_using"}
    bad = {"nope": "dismissed", "fnd_1234abcd5": "deleted", "../../etc": "dismissed", "fnd_XYZ": "dismissed", "fnd_12": "dismissed"}
    assert parse_cookie(encode({**good, **bad})) == good


def test_a_tampered_cookie_cannot_grow_without_bound():
    many = {f"fnd_{i:08x}": "dismissed" for i in range(500)}
    assert len(parse_cookie(encode(many))) <= MAX_DECISIONS


# ---------------------------------------------------------------- sample data only
def test_health_says_it_is_the_hosted_demo(tmp_path):
    h = instance(tmp_path).get("/api/health").json()
    assert h["hosted_demo"] is True


def test_uploads_are_refused_with_a_friendly_message(tmp_path):
    c = instance(tmp_path)
    r = c.post("/api/analyze", files={"file": ("statement.csv", b"Date,Description,Amount\n2026-01-01,Netflix,-15.49\n", "text/csv")})
    assert r.status_code in (403, 413) and r.json()["error"]["code"] == "hosted_demo" and r.json()["error"]["message"] == HOSTED_MESSAGE
    assert not c.cookies.get("subsweep_session")


def test_a_big_body_is_cut_off_before_it_is_read(tmp_path):
    r = instance(tmp_path).post("/api/analyze", files={"file": ("s.csv", b"x" * 200_000, "text/csv")})
    assert r.status_code == 413 and r.json()["error"]["code"] == "hosted_demo"


def test_the_sample_statement_works_and_is_labelled_as_demo_data(tmp_path):
    c = instance(tmp_path)
    r = c.post("/api/analyze", data={"demo": "true"})
    d = r.json()
    assert r.status_code == 200 and d["source"]["kind"] == "demo" and d["summary"]["active_subscriptions"] == 10 and len(d["findings"]) == 7
    assert d["paypal"]["mode"] == "mock"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie


def test_nothing_is_loaded_until_the_visitor_asks_for_the_sample(tmp_path):
    c = instance(tmp_path)
    assert c.get("/api/analysis").status_code == 404
    c.cookies.set("subsweep_session", "some-other-value")
    assert c.get("/api/analysis").status_code == 404


# ---------------------------------------------------------------- no memory shared between requests
def test_a_visitor_keeps_working_when_the_host_switches_server_instances(tmp_path):
    first = instance(tmp_path, "a")
    d = first.post("/api/analyze", data={"demo": "true"}).json()
    fid = next(f["id"] for f in d["findings"] if f["type"] == "DUPLICATE")
    sub_id = d["subscriptions"][0]["id"]
    second = visit(first, instance(tmp_path, "b"))                      # a different instance that has never seen this visitor
    assert second.get("/api/analysis").status_code == 200
    assert second.get(f"/api/subscriptions/{sub_id}/history").status_code == 200
    assert second.get("/api/paypal").json()["mode"] == "mock"
    assert second.post(f"/api/findings/{fid}/explain").status_code == 200
    assert second.post(f"/api/findings/{fid}/draft").json()["sent"] is False
    assert second.post("/api/ask", json={"question": "What do I spend on streaming?"}).status_code == 200


def test_decisions_follow_the_visitor_across_instances_but_never_leak_to_others(tmp_path):
    alice, bob = instance(tmp_path, "a"), instance(tmp_path, "b")
    fid = next(f["id"] for f in alice.post("/api/analyze", data={"demo": "true"}).json()["findings"] if f["type"] == "DUPLICATE")
    bob.post("/api/analyze", data={"demo": "true"})

    r = alice.post(f"/api/findings/{fid}/dismiss", json={"reason": "dismissed"})
    assert r.status_code == 200 and DECISIONS_COOKIE in r.headers["set-cookie"]
    status = lambda client: next(f["status"] for f in client.get("/api/analysis").json()["findings"] if f["id"] == fid)

    assert status(alice) == "dismissed"
    assert status(bob) == "open"                                         # another visitor is unaffected
    assert status(visit(alice, instance(tmp_path, "c"))) == "dismissed"   # and Alice's decision survives a new instance

    alice.post(f"/api/findings/{fid}/restore")
    assert status(alice) == "open"
    alice.post(f"/api/findings/{fid}/dismiss", json={"reason": "dismissed"})
    alice.request("DELETE", "/api/dismissals")
    assert status(alice) == "open"


def test_still_using_keeps_its_rule_in_the_hosted_build(tmp_path):
    c = instance(tmp_path)
    d = c.post("/api/analyze", data={"demo": "true"}).json()
    dup = next(f["id"] for f in d["findings"] if f["type"] == "DUPLICATE")
    assert c.post(f"/api/findings/{dup}/dismiss", json={"reason": "still_using"}).status_code == 400


def test_the_hosted_build_writes_nothing_to_the_working_directory(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    c = instance(tmp_path)
    d = c.post("/api/analyze", data={"demo": "true"}).json()
    c.post(f"/api/findings/{d['findings'][0]['id']}/explain")
    c.post("/api/ask", json={"question": "How much could I save?"})
    c.request("DELETE", "/api/analysis")
    assert list(work.iterdir()) == []                                    # no data/, no logs/: a serverless disk is read-only


def test_the_root_entrypoint_builds_the_hosted_app(monkeypatch):
    import importlib.util
    from pathlib import Path
    monkeypatch.setenv("PAYPAL_MODE", "live")                           # a hosted demo must override this
    spec = importlib.util.spec_from_file_location("vercel_entry", Path(__file__).resolve().parent.parent / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    h = TestClient(mod.app).get("/api/health").json()
    assert h["hosted_demo"] is True and h["paypal_mode"] == "mock" and h["ai"]["mode"] == "offline"


class _Decisions:
    def __init__(self, d):
        self._d = d

    def dismissals(self):
        return dict(self._d)


def test_two_visitors_views_are_independent_objects_even_when_alive_at_once(tmp_path):
    """Concurrent requests share an instance. A view that were shared would let one visitor's decisions change what another is shown mid-request."""
    c = instance(tmp_path)
    fid = next(f["id"] for f in c.post("/api/analyze", data={"demo": "true"}).json()["findings"] if f["type"] == "DUPLICATE")
    view = c.app.state.hosted_view
    alice = view(_Decisions({fid: "dismissed"}))
    bob = view(_Decisions({}))                                              # built after Alice's, while hers is still in use
    assert alice is not bob and alice.report is not bob.report
    status = lambda a: next(f.status for f in a.report.findings if f.id == fid)
    assert status(alice) == "dismissed" and status(bob) == "open"

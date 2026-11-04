import json
import threading

from subsweep.ai import AuditLog


def test_each_record_is_one_json_line_with_id_and_timestamp(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.record(feature="explain", status="ok", request={"user": "hi"}, response="hello")
    log.record(feature="qa", status="blocked:investment_advice")
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    rows = [json.loads(l) for l in lines]
    assert len(rows) == 2 and rows[0]["feature"] == "explain" and rows[0]["response"] == "hello"
    assert rows[0]["id"] != rows[1]["id"] and rows[0]["ts"].endswith("+00:00")


def test_personal_data_and_keys_are_scrubbed_on_write(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-SECRETSECRET99")
    log = AuditLog(tmp_path / "a.jsonl")
    log.record(feature="x", status="ok", request={"user": "card 4111 1111 1111 1111 and pat@example.com"}, note="key sk-ant-api03-SECRETSECRET99")
    text = (tmp_path / "a.jsonl").read_text()
    assert "4111" not in text and "pat@example.com" not in text and "SECRETSECRET99" not in text


def test_read_returns_the_latest_rows(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    for i in range(5):
        log.record(feature="f", status="ok", n=i)
    assert [r["n"] for r in log.read(limit=2)] == [3, 4] and AuditLog(tmp_path / "none.jsonl").read() == []


def test_log_rotates_when_it_gets_too_big(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl", max_bytes=400)
    for i in range(10):
        log.record(feature="f", status="ok", pad="x" * 100, n=i)
    assert (tmp_path / "a.jsonl.1").exists() and (tmp_path / "a.jsonl").stat().st_size <= 400


def test_path_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBSWEEP_AI_LOG", str(tmp_path / "env.jsonl"))
    AuditLog().record(feature="f", status="ok")
    assert (tmp_path / "env.jsonl").exists()


def test_concurrent_writes_stay_whole_lines(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    threads = [threading.Thread(target=lambda i=i: [log.record(feature="f", status="ok", n=i, pad="y" * 50) for _ in range(20)]) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    rows = (tmp_path / "a.jsonl").read_text().splitlines()
    assert len(rows) == 120 and all(json.loads(r)["feature"] == "f" for r in rows)


def test_an_api_key_of_any_shape_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "custom-key-ABCDEF1234567890")
    AuditLog(tmp_path / "a.jsonl").record(feature="x", status="ok", note="leaked custom-key-ABCDEF1234567890 here")
    text = (tmp_path / "a.jsonl").read_text()
    assert "ABCDEF" not in text and "1234567890" not in text and "[SECRET]" in text


def test_writes_hold_the_lock(tmp_path):
    class Spy:
        entered = 0

        def __enter__(self):
            Spy.entered += 1

        def __exit__(self, *a):
            return False
    log = AuditLog(tmp_path / "a.jsonl")
    log._lock = Spy()
    log.record(feature="f", status="ok")
    assert Spy.entered == 1

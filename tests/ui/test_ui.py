"""Real-browser tests (Chromium via Playwright). Skipped automatically when node or Playwright isn't available.

pytest starts a live SubSweep server on a free port (temp database, AI off, no PayPal credentials) and runs tests/ui/smoke.js
against it. Each browser check is reported as its own test below.
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from subsweep.ai import AIAssistant, AuditLog
from subsweep.api.app import create_app
from subsweep.demo import demo_statement_csv

pytestmark = pytest.mark.ui
HERE = Path(__file__).resolve().parent

CHECKS = [
    "landing_loads_clean", "skip_link_and_keyboard", "theme_toggle_persists", "scroll_changes_the_theme_and_unravels",
    "reduced_motion_shows_finished_states", "demo_flow_and_overview", "no_horizontal_scroll_at_any_width",
    "hero_descenders_not_clipped", "subscriptions_sort_filter_expand", "findings_ai_dismiss_undo_draft", "still_using_only_on_forgotten", "ask_flow_and_guardrails",
    "upload_real_file_and_error_states", "column_mapper_dialog", "empty_state_for_short_statements", "accessible_names_and_headings",
    "hostile_text_cannot_run_script", "mobile_navigation", "forget_statement",
]


def _node_env():
    node = shutil.which("node")
    if not node:
        return None
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return None
    if not root or not (Path(root) / "playwright").exists():
        return None
    return {**os.environ, "NODE_PATH": root}


@pytest.fixture(scope="module")
def browser_results(tmp_path_factory):
    env = _node_env()
    if env is None:
        pytest.skip("node + Playwright are not available (see the README to run the browser tests)")
    tmp = tmp_path_factory.mktemp("ui")
    for k in ("PAYPAL_MODE", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "ANTHROPIC_API_KEY", "SUBSWEEP_AI"):
        env.pop(k, None)
    fx = tmp / "fixtures"
    fx.mkdir()
    (fx / "demo_statement.csv").write_text(demo_statement_csv())
    (fx / "notes.pdf").write_bytes(b"%PDF-1.7 not a statement")
    (fx / "fake.csv").write_bytes(b"%PDF-1.7\x00\x00 pretending to be a csv")
    (fx / "odd_headers.csv").write_text("When,What,HowMuch\n" + "".join(f"2026-{m:02d}-05,NETFLIX.COM,-15.49\n" for m in range(1, 7)))
    (fx / "tiny.csv").write_text("Date,Description,Amount\n2026-01-01,Coffee,-3.50\n2026-01-02,Lunch,-9.00\n")
    (fx / "<img src=x onerror=window.__pwned=1>.csv").write_text(
        "Date,Description,Amount\n" + "".join(f'2026-{m:02d}-05,"<img src=x onerror=window.__pwned=1> CLOUD",-9.99\n' for m in range(1, 7)))
    (fx / "xss.csv").write_text((fx / "<img src=x onerror=window.__pwned=1>.csv").read_text())

    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    app = create_app(tmp / "db.sqlite", AIAssistant(None, AuditLog(tmp / "audit.jsonl")))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    try:
        proc = subprocess.run(["node", str(HERE / "smoke.js"), f"http://127.0.0.1:{port}/", str(fx)], capture_output=True, text=True, timeout=420, env=env)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    out = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    try:
        data = json.loads(out)
    except ValueError:
        pytest.fail(f"browser run produced no results.\nstdout: {proc.stdout[-800:]}\nstderr: {proc.stderr[-800:]}")
    if "fatal" in data:
        pytest.fail("browser run crashed: " + data["fatal"][:600])
    return {r["name"]: r for r in data["results"]}


@pytest.mark.parametrize("name", CHECKS)
def test_browser_check(browser_results, name):
    assert name in browser_results, f"check '{name}' did not run"
    r = browser_results[name]
    assert r["ok"], f"{name}: {r['detail']}"


def test_every_check_is_listed_here(browser_results):
    assert set(browser_results) == set(CHECKS)

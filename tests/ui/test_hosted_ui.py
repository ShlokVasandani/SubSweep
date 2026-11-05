"""Real-browser test of the hosted (serverless) build: three separate server processes behind a round-robin proxy, so consecutive requests from
one browser are answered by different processes that share nothing. Everything a visitor does with their own uploaded statement must keep working.
Skipped automatically when node or Playwright isn't available."""
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.ui.test_ui import _node_env

pytestmark = pytest.mark.ui
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(url, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return
        except Exception:
            time.sleep(0.4)
    raise AssertionError(f"{url} did not come up")


@pytest.fixture(scope="module")
def hosted_run(tmp_path_factory):
    env = _node_env()
    if env is None:
        pytest.skip("node + Playwright are not available (see the README to run the browser tests)")
    tmp = tmp_path_factory.mktemp("hosted")
    (tmp / "odd_headers.csv").write_text("When,What,HowMuch\n" + "".join(f"2026-{m:02d}-05,NETFLIX.COM,-15.49\n" for m in range(1, 7)))
    (tmp / "notes.pdf.csv").write_bytes(b"%PDF-1.7 not a statement")
    backends, procs = [free_port() for _ in range(3)], []
    proxy_port = free_port()
    try:
        for port in backends:
            procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", str(port), "--log-level", "warning"], cwd=ROOT,
                                          env={**env, "PAYPAL_MODE": "mock"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for port in backends:
            wait_for(f"http://127.0.0.1:{port}/api/health")
        procs.append(subprocess.Popen([sys.executable, str(HERE / "rr_proxy.py"), str(proxy_port), *map(str, backends)]))
        wait_for(f"http://127.0.0.1:{proxy_port}/api/health")
        run = subprocess.run(["node", str(HERE / "hosted_e2e.js"), f"http://127.0.0.1:{proxy_port}/", str(tmp), str(ROOT / "sample_data" / "demo_statement.csv")],
                             capture_output=True, text=True, timeout=280, env=env)
        log = json.load(urllib.request.urlopen(f"http://127.0.0.1:{proxy_port}/__proxy_log"))
        return run, log
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=15)


def test_every_browser_check_passes_against_three_instances(hosted_run):
    run, _ = hosted_run
    fails = [ln for ln in run.stdout.splitlines() if ln.startswith("FAIL")]
    assert run.returncode == 0 and not fails, f"{fails}\n{run.stdout[-1200:]}\n{run.stderr[-600:]}"
    assert "RESULT" in run.stdout


def test_consecutive_requests_really_were_served_by_different_instances(hosted_run):
    _, log = hosted_run
    api = [r["backend"] for r in log if r["path"].startswith("/api/")]
    assert len(api) >= 20 and set(api) == {0, 1, 2}
    switches = sum(1 for a, b in zip(api, api[1:]) if a != b)
    assert switches >= 0.9 * (len(api) - 1), f"only {switches} of {len(api) - 1} consecutive API calls changed instance"

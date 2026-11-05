"""Round-robin reverse proxy for the hosted-build browser test: every request goes to the NEXT backend, so consecutive requests from one browser
are answered by different server processes, which is what a serverless host does. GET /__proxy_log returns what it forwarded.

    python rr_proxy.py <listen-port> <backend-port> [<backend-port> ...]
"""
import http.client
import itertools
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

listen, ports = int(sys.argv[1]), [int(p) for p in sys.argv[2:]]
rotations = {"api": itertools.cycle(range(len(ports))), "static": itertools.cycle(range(len(ports)))}   # API calls rotate on their own, so static files cannot skew them
lock, log = threading.Lock(), []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self):
        if self.path == "/__proxy_log":
            data = json.dumps(log).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        with lock:
            i = next(rotations["api" if self.path.startswith("/api/") else "static"])
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        conn = http.client.HTTPConnection("127.0.0.1", ports[i], timeout=120)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")}
        conn.request(self.command, self.path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        with lock:
            log.append({"backend": i, "path": self.path.split("?")[0], "status": resp.status})
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("connection", "transfer-encoding", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_DELETE = _forward

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True


Server(("127.0.0.1", listen), Handler).serve_forever()

"""The Docker Locust file can drive the Agent API contract."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4


def test_locustfile_runs_agent_flow_against_http_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if self.path == "/conversations":
                status, body = 201, {"conversation_id": str(uuid4())}
            elif self.path == "/chat":
                received.append((self.headers.get("X-Agent-Token"), payload))
                status, body = 202, {"execution_id": str(uuid4()), "status": "QUEUED"}
            else:
                status, body = 404, {}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"SUCCEEDED"}')

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.update({
        "AGENT_INTERNAL_TOKEN": "test-token",
        "LOCUST_POLL_INTERVAL": "0.01",
        "LOCUST_WAIT_TIMEOUT": "1",
        "PYTHONPATH": str(root),
    })
    try:
        completed = subprocess.run(
            [
                sys.executable, "-m", "locust", "-f", str(root / "evals/locustfile.py"),
                "--headless", "--users", "2", "--spawn-rate", "2", "--run-time", "2s",
                "--only-summary", "--exit-code-on-error", "1",
                "--host", f"http://127.0.0.1:{server.server_port}",
            ],
            cwd=root, env=environment, capture_output=True, text=True, timeout=20,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert received
    assert all(token == "test-token" for token, _ in received)
    assert all(payload["llm_backend"] == "mock" and payload["mock_mode"] == "fast" for _, payload in received)
    assert len({payload["idempotency_key"] for _, payload in received}) == len(received)

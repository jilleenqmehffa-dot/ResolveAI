"""The unified runner dispatches load and fault modules with one CLI."""

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.engine import make_url

from evals import runner
from evals.clients.execution_timing import completed_run_seconds
from evals.executors import concurrency as concurrency_executor
from evals.executors import network_fault as network_executor


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    tasks = [
        {"id": "one", "customer_id": "u1", "message": "first"},
        {"id": "two", "customer_id": "u2", "message": "second"},
    ]
    task_path = tmp_path / "tasks.json"
    task_path.write_text(json.dumps(tasks), encoding="utf-8")
    database_path = tmp_path / "db.json"
    database_path.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "runner.yaml"
    config_path.write_text(
        f"tasks: {task_path}\ndatabase: {database_path}\n"
        "agent_base_url: http://agent.test\ncommerce_base_url: http://commerce.test\n"
        "toxiproxy_api: http://127.0.0.1:8474\n"
        "poll_interval: 0.001\nwait_timeout: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "test-token")
    agent_db = tmp_path / "agent.db"
    with sqlite3.connect(agent_db) as connection:
        connection.execute(
            "CREATE TABLE agent_runs (id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "error_code TEXT, created_at TEXT NOT NULL, finished_at TEXT)"
        )
    monkeypatch.setenv("AGENT_DATABASE_URL", f"sqlite+pysqlite:///{agent_db}")
    monkeypatch.setenv("DATA_PLATFORM_RUNS_DIR", str(tmp_path / "runs"))
    return config_path, tmp_path / "results.json"


def finish_fake_run(execution_id, seconds=0.2):
    database_url = os.environ["AGENT_DATABASE_URL"]
    database_path = make_url(database_url).database
    created = datetime(2026, 11, 15)
    finished = created + timedelta(seconds=seconds)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?)",
            (execution_id.replace("-", ""), "SUCCEEDED", None,
             created.isoformat(" "), finished.isoformat(" ")),
        )


def test_completed_run_clock_reversal_does_not_become_a_task_failure():
    assert completed_run_seconds({
        "created_at": "2026-11-15T12:33:51.500000+00:00",
        "updated_at": "2026-11-15T12:33:50.950000+00:00",
    }) == 0.0


def test_collect_run_results_reads_late_terminal_state_and_marks_timeout(inputs):
    completed_id, pending_id = str(uuid4()), str(uuid4())
    database_path = make_url(os.environ["AGENT_DATABASE_URL"]).database
    created = "2026-11-15 00:00:00.000000"
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO agent_runs VALUES (?, 'RUNNING', NULL, ?, NULL)",
            [(run_id.replace("-", ""), created) for run_id in (completed_id, pending_id)],
        )

    def finish_later():
        time.sleep(0.03)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE agent_runs SET status = 'FAILED', error_code = 'mock_failed', "
                "finished_at = '2026-11-15 00:00:00.300000' WHERE id = ?",
                (completed_id.replace("-", ""),),
            )

    thread = threading.Thread(target=finish_later)
    thread.start()
    results = {
        0: {"execution_id": completed_id, "status": "QUEUED", "error": None, "total_seconds": None},
        1: {"execution_id": pending_id, "status": "QUEUED", "error": None, "total_seconds": None},
    }
    try:
        concurrency_executor.collect_run_results(
            results, os.environ["AGENT_DATABASE_URL"], interval=0.01, timeout=0.1,
        )
    finally:
        thread.join(timeout=1)
    assert results[0]["status"] == "FAILED"
    assert results[0]["error"] == "mock_failed"
    assert results[0]["total_seconds"] == pytest.approx(0.3)
    assert results[1]["status"] == "COMPLETION_TIMEOUT"
    assert results[1]["total_seconds"] is None


def test_concurrency_checks_agent_database_before_submitting(inputs, monkeypatch):
    config_path, output = inputs
    monkeypatch.setenv("AGENT_DATABASE_URL", f"sqlite+pysqlite:///{output.parent / 'missing.db'}")
    with pytest.raises(RuntimeError, match="Cannot read Agent database before load"):
        runner.main([
            "--config", str(config_path), "--mode", "concurrency", "--task-id", "one",
            "--output", str(output),
        ])
    assert not output.exists()


@pytest.mark.parametrize("fail_chat", [False, True])
@pytest.mark.parametrize("explicit_output", [False, True])
def test_concurrency_uses_locust_and_keeps_runner_results(inputs, monkeypatch, capsys, fail_chat, explicit_output):
    config_path, output = inputs
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/conversations":
                status, body = 201, {"conversation_id": str(uuid4())}
            elif self.path == "/chat":
                received.append(payload)
                if fail_chat and payload["message"] == "first":
                    status, body = 503, {"detail": "busy"}
                else:
                    execution_id = str(uuid4())
                    finish_fake_run(execution_id)
                    status, body = 202, {"execution_id": execution_id, "status": "QUEUED"}
            else:
                status, body = 404, {}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            raise AssertionError("load test must not poll the Agent API")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    reports_dir = output.parent / "reports"
    monkeypatch.setattr(runner, "REPORTS_DIR", reports_dir)
    config_path.write_text(
        config_path.read_text().replace("http://agent.test", f"http://127.0.0.1:{server.server_port}")
    )
    args = [
        "--config", str(config_path), "--mode", "concurrency", "--all",
        "--concurrency", "2", "--repeat", "2", "--llm-backend", "mock",
        "--mock-mode", "latency",
    ]
    if explicit_output:
        args.extend(("--output", str(output)))
    try:
        assert runner.main(args) == (1 if fail_chat else 0)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    actual_output = output if explicit_output else next((reports_dir / "concurrency").glob("*.json"))
    rows = json.loads(actual_output.read_text())
    assert [(row["task_id"], row["repetition"]) for row in rows] == [
        ("one", 1), ("two", 1), ("one", 2), ("two", 2),
    ]
    assert {row["status"] for row in rows} == ({"SUCCEEDED", "ERROR"} if fail_chat else {"SUCCEEDED"})
    assert len({row["conversation_id"] for row in rows}) == 4
    assert all(row["total_seconds"] == pytest.approx(0.2) for row in rows if row["status"] == "SUCCEEDED")
    assert len({payload["idempotency_key"] for payload in received}) == 4
    assert {payload["llm_backend"] for payload in received} == {"mock"}
    assert {payload["mock_mode"] for payload in received} == {"latency"}
    output_text = capsys.readouterr().out
    expected_successes = 2 if fail_chat else 4
    assert f"Locust: 2 users, 4 executions, {expected_successes} completed" in output_text
    assert f"{expected_successes} succeeded" in output_text
    assert f"{expected_successes / 0.2:.2f} completed tasks/s" in output_text
    assert f"success rate {expected_successes / 4:.1%}" in output_text
    assert "E2E p95 200 ms" in output_text
    assert "HTTP req/s" not in output_text
    receipt = json.loads(next((output.parent / "runs").glob("*.json")).read_text())
    assert receipt["result_file"] == str(actual_output.resolve())
    assert receipt["summary"]["total"] == 4
    assert receipt["mode"] == "concurrency"
    assert receipt["mock_mode"] == "latency"


def test_duration_mode_cycles_cases_and_drains_started_flows(inputs):
    config_path, output = inputs
    submitted = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if self.path == "/conversations":
                status, body = 201, {"conversation_id": str(uuid4())}
            elif self.path == "/chat":
                submitted.append(payload["idempotency_key"])
                execution_id = str(uuid4())
                finish_fake_run(execution_id, seconds=0.02)
                status, body = 202, {"execution_id": execution_id, "status": "QUEUED"}
            else:
                status, body = 404, {}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            raise AssertionError("load test must not poll the Agent API")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_path.write_text(config_path.read_text().replace("http://agent.test", f"http://127.0.0.1:{server.server_port}"))
    try:
        assert runner.main([
            "--config", str(config_path), "--mode", "concurrency", "--all",
            "--run-mode", "duration", "--duration", "0.3", "--repeat", "1",
            "--concurrency", "2", "--output", str(output),
        ]) == 0
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    rows = json.loads(output.read_text())
    assert len(rows) > 2  # --repeat 1 does not cap a duration run.
    assert {row["task_id"] for row in rows} == {"one", "two"}
    assert all(row["status"] == "SUCCEEDED" for row in rows)
    assert len(rows) == len(submitted)  # Every flow started before the deadline was drained.
    assert len(set(submitted)) == len(submitted)
    receipt = json.loads(next((output.parent / "runs").glob("*.json")).read_text())
    assert receipt["run_mode"] == "duration"
    assert receipt["duration_seconds"] == 0.3
    assert receipt["repeat"] is None
    assert receipt["summary"]["total"] == len(rows)


def test_runner_web_ui_shows_the_same_finite_workload(inputs):
    config_path, output = inputs

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path == "/conversations":
                status, body = 201, {"conversation_id": str(uuid4())}
            elif self.path == "/chat":
                execution_id = str(uuid4())
                finish_fake_run(execution_id, seconds=0.02)
                status, body = 202, {"execution_id": execution_id, "status": "QUEUED"}
            else:
                status, body = 404, {}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            raise AssertionError("load test must not poll the Agent API")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_path.write_text(config_path.read_text().replace("http://agent.test", f"http://127.0.0.1:{server.server_port}"))
    config_path.write_text(config_path.read_text().replace("poll_interval: 0.001", "poll_interval: 0.1"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        web_port = listener.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable, "-m", "evals.runner", "--config", str(config_path),
            "--mode", "concurrency", "--task-id", "one", "--repeat", "2",
            "--concurrency", "1", "--web-ui", "--web-port", str(web_port),
            "--output", str(output),
        ],
        env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not output.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.1)
        assert output.exists(), process.communicate(timeout=1)
        rows = json.loads(output.read_text())
        assert [(row["task_id"], row["repetition"]) for row in rows] == [("one", 1), ("one", 2)]
        assert all(row["total_seconds"] == pytest.approx(0.02) for row in rows)
        response = httpx.get(f"http://127.0.0.1:{web_port}/stats/requests", timeout=3)
        response.raise_for_status()
        stats = response.json()["stats"]
        assert any(row["method"] == "FLOW" and row["name"] == "agent execution" and row["num_requests"] == 2 and row["avg_response_time"] == pytest.approx(20, abs=1) for row in stats)
        summary = httpx.get(f"http://127.0.0.1:{web_port}/resolveai/results", timeout=3)
        summary.raise_for_status()
        assert all(label in summary.text for label in ("完成任务", "完成 TPS", "成功率", "E2E P95"))
        assert "2" in summary.text
        assert "100.0%" in summary.text
        assert "20 ms" in summary.text
        assert process.poll() is None
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_network_fault_mode_restores_proxy_even_if_run_raises(inputs, monkeypatch):
    config_path, output = inputs
    operations = []
    monkeypatch.setattr(network_executor, "set_fault", lambda api, fault, ms: operations.append(("set", api, fault, ms)))
    monkeypatch.setattr(network_executor, "reset_fault", lambda api: operations.append(("reset", api)))

    async def fail_submit(*_args):
        raise RuntimeError("network request failed")

    monkeypatch.setattr(network_executor, "submit_case", fail_submit)
    with pytest.raises(RuntimeError, match="network request failed"):
        runner.main([
            "--config", str(config_path), "--mode", "network-fault", "--task-id", "one",
            "--fault", "latency", "--fault-ms", "250", "--llm-backend", "mock",
            "--output", str(output),
        ])
    assert operations == [
        ("set", "http://127.0.0.1:8474", "latency", 250),
        ("reset", "http://127.0.0.1:8474"),
    ]


def test_network_fault_mode_records_scenario_and_selected_model(inputs, monkeypatch):
    config_path, output = inputs
    operations = []
    monkeypatch.setattr(network_executor, "set_fault", lambda api, fault, ms: operations.append("set"))
    monkeypatch.setattr(network_executor, "reset_fault", lambda api: operations.append("reset"))
    seen_backends = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert operations == ["set"]
        if request.url.path == "/conversations":
            return httpx.Response(201, json={"conversation_id": str(uuid4())})
        if request.url.path == "/chat":
            seen_backends.append(json.loads(request.content)["llm_backend"])
            return httpx.Response(202, json={"execution_id": str(uuid4()), "status": "QUEUED"})
        return httpx.Response(200, json={"status": "SUCCEEDED"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        network_executor.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert runner.main([
        "--config", str(config_path), "--mode", "network-fault", "--task-id", "one",
        "--fault", "disconnect", "--fault-ms", "250", "--repeat", "2",
        "--llm-backend", "mock", "--output", str(output),
    ]) == 0
    assert operations == ["set", "reset"]
    rows = json.loads(output.read_text())
    assert len(rows) == 2
    assert [row["repetition"] for row in rows] == [1, 2]
    assert {row["status"] for row in rows} == {"SUCCEEDED"}
    assert {row["fault"] for row in rows} == {"disconnect"}
    assert {row["fault_ms"] for row in rows} == {250}
    assert seen_backends == ["mock", "mock"]
    receipt = json.loads(next((output.parent / "runs").glob("*.json")).read_text())
    assert receipt["mode"] == "network-fault"
    assert receipt["fault"] == "disconnect"
    assert receipt["llm_backend"] == "mock"


def test_network_fault_mode_keeps_failed_request_and_restores_proxy(inputs, monkeypatch):
    config_path, output = inputs
    operations = []
    monkeypatch.setattr(network_executor, "set_fault", lambda *_args: operations.append("set"))
    monkeypatch.setattr(network_executor, "reset_fault", lambda *_args: operations.append("reset"))

    async def handler(request: httpx.Request) -> httpx.Response:
        assert operations == ["set"]
        if request.url.path == "/conversations":
            return httpx.Response(201, json={"conversation_id": str(uuid4())})
        return httpx.Response(503, json={"detail": "fault injected"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        network_executor.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert runner.main([
        "--config", str(config_path), "--mode", "network-fault", "--task-id", "one",
        "--fault", "timeout", "--fault-ms", "500", "--output", str(output),
    ]) == 1
    row = json.loads(output.read_text())[0]
    assert row["status"] == "ERROR"
    assert "503" in row["error"]
    assert row["fault"] == "timeout"
    assert row["fault_ms"] == 500
    assert operations == ["set", "reset"]


def test_fault_required_only_for_network_mode(inputs):
    config_path, output = inputs
    with pytest.raises(SystemExit):
        runner.main([
            "--config", str(config_path), "--mode", "network-fault", "--all",
            "--output", str(output),
        ])


def test_dry_run_previews_selection_without_token_or_output(inputs, monkeypatch, capsys):
    config_path, _output = inputs
    monkeypatch.delenv("AGENT_INTERNAL_TOKEN", raising=False)
    assert runner.main([
        "--config", str(config_path), "--mode", "concurrency", "--all",
        "--limit", "1", "--repeat", "3", "--llm-backend", "mock", "--dry-run",
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["task_ids"] == ["one"]
    assert preview["planned_requests"] == 3
    assert preview["llm_backend"] == "mock"

"""The unified runner dispatches load and fault modules with one CLI."""

import json
from uuid import uuid4

import httpx
import pytest

from evals import runner
from evals.clients import load_client
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
    monkeypatch.setenv("DATA_PLATFORM_RUNS_DIR", str(tmp_path / "runs"))
    return config_path, tmp_path / "results.json"


def test_concurrency_mode_forwards_mock_and_writes_results(inputs, monkeypatch):
    config_path, output = inputs
    seen_backends = []
    seen_modes = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/conversations":
            return httpx.Response(201, json={"conversation_id": str(uuid4())})
        if request.url.path == "/chat":
            payload = json.loads(request.content)
            seen_backends.append(payload["llm_backend"])
            seen_modes.append(payload["mock_mode"])
            return httpx.Response(202, json={"execution_id": str(uuid4()), "status": "QUEUED"})
        return httpx.Response(200, json={"status": "SUCCEEDED"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        load_client.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert runner.main([
        "--config", str(config_path), "--mode", "concurrency", "--all",
        "--concurrency", "2", "--repeat", "2", "--llm-backend", "mock",
        "--mock-mode", "latency",
        "--output", str(output),
    ]) == 0
    rows = json.loads(output.read_text(encoding="utf-8"))
    assert len(rows) == 4
    assert {row["status"] for row in rows} == {"SUCCEEDED"}
    assert seen_backends == ["mock"] * 4
    assert seen_modes == ["latency"] * 4
    receipt = json.loads(next((output.parent / "runs").glob("*.json")).read_text())
    assert receipt["mode"] == "concurrency"
    assert receipt["mock_mode"] == "latency"
    assert receipt["summary"]["total"] == 4
    assert len(receipt["scenario_config_sha256"]) == 64
    assert receipt["dataset_version"] is None


def test_concurrency_without_output_writes_to_mode_report_directory(inputs, monkeypatch):
    config_path, output = inputs
    reports_dir = output.parent / "reports"
    monkeypatch.setattr(runner, "REPORTS_DIR", reports_dir)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/conversations":
            return httpx.Response(201, json={"conversation_id": str(uuid4())})
        if request.url.path == "/chat":
            return httpx.Response(202, json={"execution_id": str(uuid4()), "status": "QUEUED"})
        return httpx.Response(200, json={"status": "SUCCEEDED"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        load_client.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert runner.main([
        "--config", str(config_path), "--mode", "concurrency", "--all",
        "--llm-backend", "mock",
    ]) == 0
    reports = list((reports_dir / "concurrency").glob("*.json"))
    assert len(reports) == 1
    assert [row["status"] for row in json.loads(reports[0].read_text())] == ["SUCCEEDED", "SUCCEEDED"]
    assert not (reports_dir / "agent-task").exists()
    assert not (reports_dir / "network-fault").exists()
    receipt = json.loads(next((output.parent / "runs").glob("*.json")).read_text())
    assert receipt["result_file"] == str(reports[0].resolve())


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

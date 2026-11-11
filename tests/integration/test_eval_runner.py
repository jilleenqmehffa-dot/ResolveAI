"""Exercise the runner from task JSON through real normalizers and graders."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from evals import runner
from evals.adapter.commerce_adapter import CommerceSnapshot
from evals.adapter.trace_adapter import AgentTrace


def test_example_config_selects_test_split_in_split_order():
    root = Path(__file__).resolve().parents[2]
    tasks = runner.load_tasks(root / "evals/datasets/tasks.json")
    selected = runner.select_split(
        tasks, root / "evals/datasets/split_tasks.json", "test"
    )
    assert len(selected) == 40
    assert str(selected[0]["id"]) == "5"
    assert str(selected[-1]["id"]) == "111"


def test_runner_cli_keeps_input_order_and_task_errors(tmp_path, monkeypatch):
    tasks = [
        {"id": "first", "customer_id": "u1", "message": "hello",
         "evaluation_criteria": {"actions": [], "communicate_info": []}},
        {"id": "broken", "message": "missing customer",
         "evaluation_criteria": {"actions": []}},
    ]
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(json.dumps(tasks), encoding="utf-8")
    db_path = tmp_path / "db.json"
    db_path.write_text('{"users": {}, "orders": {}}', encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"tasks: {tasks_path}\ndatabase: {db_path}\n"
        "agent_base_url: http://agent.test\ncommerce_base_url: http://commerce.test\n"
        "concurrency: 2\npoll_interval: 0.01\nwait_timeout: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "token")
    monkeypatch.setenv("DATA_PLATFORM_RUNS_DIR", str(tmp_path / "runs"))
    seen_backends = []

    async def handler(request):
        if request.url.path == "/conversations":
            return httpx.Response(201, json={"conversation_id": str(uuid4())})
        if request.url.path == "/chat":
            seen_backends.append(json.loads(request.content)["llm_backend"])
            return httpx.Response(202, json={"execution_id": str(uuid4()), "status": "QUEUED"})
        return httpx.Response(200, json={"status": "SUCCEEDED"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        runner.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    class Commerce:
        def __init__(self, customer_id, **kwargs):
            self.customer_id = customer_id

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def capture_task(self, task):
            return CommerceSnapshot(
                str(task["id"]), self.customer_id, datetime.now(UTC),
                {"user_id": self.customer_id}, [], [], {}, {}, {}, {},
            )

    class Traces:
        def __init__(self, **kwargs):
            pass

        def wait_for_traces(self, execution_id, **kwargs):
            return [AgentTrace(execution_id, "trace", "root", ())]

    monkeypatch.setattr(runner, "CommerceAdapter", Commerce)
    monkeypatch.setattr(runner, "TraceAdapter", Traces)
    output = tmp_path / "results.json"
    assert runner.main(["--config", str(config_path), "--all", "--output", str(output)]) == 1
    rows = json.loads(output.read_text(encoding="utf-8"))
    assert [row["task_id"] for row in rows] == ["first", "broken"]
    assert rows[0]["scores"] == {"task_success": 1.0, "trajectory": 1.0}
    assert rows[0]["error"] is None
    assert rows[1]["error"]["type"] == "KeyError"
    assert seen_backends == ["deepseek"]
    run_files = list((tmp_path / "runs").glob("*.json"))
    assert len(run_files) == 1
    run_record = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert run_record["summary"]["total"] == 2
    assert run_record["summary"]["scored"] == 1

    split_path = tmp_path / "split.json"
    split_path.write_text('{"test": ["broken"]}', encoding="utf-8")
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + f"split_file: {split_path}\nsplit: test\n",
        encoding="utf-8",
    )
    assert runner.main([
        "--config", str(config_path), "--task-id", "first", "--output", str(output),
        "--llm-backend", "mock",
    ]) == 0
    rows = json.loads(output.read_text(encoding="utf-8"))
    assert [row["task_id"] for row in rows] == ["first"]
    assert seen_backends == ["deepseek", "mock"]
    assert {json.loads(path.read_text())["llm_backend"] for path in (tmp_path / "runs").glob("*.json")} == {"deepseek", "mock"}

    with pytest.raises(SystemExit):
        runner.main([
            "--config", str(config_path), "--task-id", "missing", "--output", str(output)
        ])

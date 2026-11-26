"""Check task selection and the pending simulated-user executor boundary."""

import json
from pathlib import Path

from evals import runner
from evals.evaluators.agent_task import AgentExecution


def test_example_config_selects_test_split_in_split_order():
    root = Path(__file__).resolve().parents[2]
    tasks = runner.load_tasks(root / "evals/scenarios/agent_task/datasets/tasks.json")
    selected = runner.select_split(
        tasks, root / "evals/scenarios/agent_task/datasets/split_tasks.json", "test"
    )
    assert len(selected) == 40
    assert str(selected[0]["id"]) == "5"
    assert str(selected[-1]["id"]) == "111"


def test_runner_routes_agent_task_results_to_evaluator(tmp_path, monkeypatch):
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(json.dumps([{"id": "first"}]), encoding="utf-8")
    database_path = tmp_path / "db.json"
    database_path.write_text(json.dumps({"users": {"u1": {}}}), encoding="utf-8")
    oracle_path = tmp_path / "task_customers.json"
    oracle_path.write_text(json.dumps({"first": "u1"}), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"tasks: {tasks_path}\ndatabase: {database_path}\ncustomer_oracle: {oracle_path}\n"
        "agent_base_url: http://agent.test\ncommerce_base_url: http://commerce.test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "test-token")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setenv("USER_SIMULATOR_API_KEY", "test-key")
    monkeypatch.setenv("DATA_PLATFORM_RUNS_DIR", str(tmp_path / "runs"))
    output = tmp_path / "results.json"
    monkeypatch.setattr(
        runner, "execute",
        lambda scenario, token: [AgentExecution(
            task=scenario.inputs.tasks[0], task_id="first",
            error={"type": "TestError", "message": "simulated failure"},
        )],
    )

    assert runner.main(["--config", str(config_path), "--all", "--output", str(output)]) == 1
    assert json.loads(output.read_text(encoding="utf-8"))[0]["reasons"] == ["simulated failure"]

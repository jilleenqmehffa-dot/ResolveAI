"""Scenario modules own datasets, defaults, and prerequisite contracts."""

import json

import pytest

from data_platform.core import source_path
from evals import runner


@pytest.mark.parametrize(
    ("mode", "backend", "concurrency", "fault", "services"),
    [
        ("agent-task", "deepseek", 1, None, {"agent-worker", "commerce"}),
        ("concurrency", "mock", 10, None, {"agent-worker", "rabbitmq"}),
        ("network-fault", "mock", 1, "latency", {"toxiproxy", "commerce"}),
    ],
)
def test_default_scenarios_load_pinned_test_split(
    mode, backend, concurrency, fault, services, capsys
) -> None:
    assert runner.main(["--mode", mode, "--all", "--limit", "1", "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dataset_version"] == "retail-bench-v1"
    assert preview["split"] == "test"
    assert preview["task_ids"] == ["5"]
    assert preview["llm_backend"] == backend
    assert preview["concurrency"] == concurrency
    assert preview["fault"] == fault
    assert services <= set(preview["required_services"])


def test_agent_scenario_model_override_changes_worker_requirement(capsys) -> None:
    assert runner.main([
        "--mode", "agent-task", "--task-id", "12", "--llm-backend", "mock",
        "--dry-run",
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["task_ids"] == ["12"]
    assert "DEEPSEEK_API_KEY" not in preview["required_worker_env"]
    assert "LANGSMITH_API_KEY" in preview["required_client_env"]
    assert "LANGSMITH_API_KEY" in preview["required_worker_env"]
    assert "AGENT_INTERNAL_TOKEN" in preview["required_worker_env"]


def test_concurrency_mock_mode_cli_overrides_scenario_config(tmp_path, capsys) -> None:
    config = tmp_path / "concurrency.json"
    config.write_text(json.dumps({
        "tasks": "evals/scenarios/agent_task/datasets/tasks.json",
        "split_file": "evals/scenarios/agent_task/datasets/split_tasks.json",
        "split": "test",
        "database": "evals/scenarios/agent_task/datasets/db.json",
        "agent_base_url": "http://agent.test",
        "llm_backend": "mock",
        "mock_mode": "latency",
    }))
    assert runner.main([
        "--mode", "concurrency", "--config", str(config), "--all", "--limit", "1",
        "--mock-mode", "fast", "--dry-run",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["mock_mode"] == "fast"
    assert runner.main([
        "--mode", "concurrency", "--config", str(config), "--all", "--limit", "1",
        "--dry-run",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["mock_mode"] == "latency"


def test_scenario_rejects_invalid_concurrency_before_execution() -> None:
    with pytest.raises(SystemExit):
        runner.main([
            "--mode", "agent-task", "--all", "--concurrency", "0", "--dry-run",
        ])


def test_agent_scenario_rejects_fault_options() -> None:
    with pytest.raises(SystemExit):
        runner.main([
            "--mode", "agent-task", "--task-id", "12", "--fault-ms", "100",
            "--dry-run",
        ])


def test_custom_split_is_not_labeled_as_pinned_dataset(tmp_path, capsys) -> None:
    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps({"test": ["12"]}), encoding="utf-8")
    config_path = tmp_path / "scenario.yaml"
    config_path.write_text(
        f"tasks: {source_path('tasks')}\n"
        f"database: {source_path('retail')}\n"
        f"split_file: {split_path}\nsplit: test\n"
        "agent_base_url: http://agent.test\n",
        encoding="utf-8",
    )
    assert runner.main([
        "--mode", "concurrency", "--config", str(config_path), "--all", "--dry-run",
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["task_ids"] == ["12"]
    assert preview["dataset_version"] is None

"""The launcher keeps snapshot identity private from the Agent dialogue."""

from types import SimpleNamespace

import pytest

from evals.executors.user_simulate import evaluation_launcher
from evals.executors.user_simulate.orchestrator import OrchestrationResult, State
from evals.scenarios.base import resolve_customer_id


def test_private_customer_oracle_is_used_without_scenario_parsing():
    task = {"id": "task-1", "user_scenario": {"instructions": {"known_info": "Someone else"}}}
    assert resolve_customer_id(task, {"task-1": "customer-1"}) == "customer-1"


def test_private_customer_oracle_requires_an_entry():
    with pytest.raises(ValueError, match="missing private customer oracle"):
        resolve_customer_id({"id": "task-1"}, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_error", [False, True])
async def test_launcher_collects_snapshots_and_restores_even_on_agent_error(monkeypatch, agent_error):
    captured = []

    class Commerce:
        def __init__(self, customer_id, **kwargs):
            captured.append(("customer", customer_id))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def capture_task(self, task):
            captured.append(("snapshot", task["id"]))
            return object()

    class Agent:
        def __init__(self, client, token, **kwargs):
            assert token == "token"

        async def run(self, task, *, eval_run_id):
            if agent_error:
                raise RuntimeError("agent unavailable")
            return OrchestrationResult(
                str(task["id"]), f"eval-anon:{eval_run_id}", eval_run_id, State.STOPPED,
                execution_ids=["run-1", "run-2"],
                history=[("user", "Hello"), ("agent", "Hi")],
            )

    monkeypatch.setattr(evaluation_launcher, "CommerceAdapter", Commerce)
    monkeypatch.setattr(evaluation_launcher, "Orchestrator", Agent)
    class Engine:
        def dispose(self):
            captured.append(("dispose",))

    monkeypatch.setattr(evaluation_launcher, "with_engine", lambda url: Engine())
    monkeypatch.setattr(
        evaluation_launcher, "capture_customer",
        lambda engine, customer_id: captured.append(("checkpoint", customer_id)) or object(),
    )
    monkeypatch.setattr(
        evaluation_launcher, "restore_customer",
        lambda engine, checkpoint: captured.append(("restore",)) or True,
    )
    scenario = SimpleNamespace(
        mode="agent-task", concurrency=1, test_database_url=None,
        llm_backend="mock", setting=lambda name: "sqlite://",
        inputs=SimpleNamespace(
            tasks=[{"id": "task-1"}], database={}, customer_oracle={"task-1": "u1"},
            config={"agent_base_url": "http://agent.test", "commerce_base_url": "http://commerce.test"},
        ),
    )
    observed = (await evaluation_launcher.launch_async(scenario, "token"))[0]

    assert observed.eval_run_id is not None
    assert observed.execution_ids == (() if agent_error else ("run-1", "run-2"))
    if agent_error:
        assert observed.error == {"type": "RuntimeError", "message": "agent unavailable"}
    else:
        assert observed.history == (("user", "Hello"), ("agent", "Hi"))
        assert observed.error is None
    assert captured == (
        [("checkpoint", "u1"), ("customer", "u1"), ("snapshot", "task-1")]
        + ([] if agent_error else [("snapshot", "task-1")])
        + [("restore",), ("dispose",)]
    )

"""A faulted Agent task keeps the full evaluator path and restores the proxy."""

from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace

from evals import executors
from evals.evaluators import agent_task
from evals.evaluators.agent_task import AgentExecution, evaluate


def test_faulted_agent_task_collects_tool_rows_and_restores_proxy(monkeypatch):
    events = []
    observed = AgentExecution(
        task={"id": "12"}, task_id="12", execution_ids=("run-id",),
    )

    @contextmanager
    def fault_environment(scenario):
        events.append("inject")
        try:
            yield SimpleNamespace(as_dict=lambda: {"target": "commerce"})
        finally:
            events.append("reset")

    monkeypatch.setattr(executors, "fault_environment", fault_environment)
    monkeypatch.setattr(
        executors, "launch_evaluation",
        lambda scenario, token: events.append("evaluate") or [observed],
    )
    monkeypatch.setattr(
        executors, "capture_tool_executions",
        lambda database_url, execution_ids: events.append("tools") or {"records": ["saved"]},
    )
    scenario = SimpleNamespace(
        mode="agent-task", fault="disconnect", fault_target="commerce", fault_ms=0,
        setting=lambda name: "postgresql://agent" if name == "AGENT_DATABASE_URL" else None,
    )

    assert executors.execute(scenario, "token") == [observed]
    assert events == ["inject", "evaluate", "tools", "reset"]
    assert observed.proxy_observation == {"target": "commerce"}
    assert observed.tool_execution_observation == {"records": ["saved"]}


def test_failed_fault_task_still_captures_langsmith_trace():
    observed = AgentExecution(
        task={"id": "12"}, task_id="12", fault="timeout",
        eval_run_id="eval-id", execution_ids=("run-id",),
        error={"type": "TimeoutError", "message": "Agent timed out"},
    )
    trace = SimpleNamespace(
        trace_id="trace-id",
        runs=(SimpleNamespace(error="Commerce unavailable"),),
    )

    class Adapter:
        def wait_for_eval_traces(self, eval_run_id, execution_ids, *, timeout):
            assert (eval_run_id, execution_ids) == ("eval-id", ["run-id"])
            return [trace]

    row = evaluate(observed, trace_adapter=Adapter())
    assert row["scores"] is None
    assert row["trace_observation"] == {
        "trace_ids": ["trace-id"], "run_count": 1,
        "errors": ["Commerce unavailable"],
    }


def test_faulted_task_scores_final_business_state_even_after_agent_error(monkeypatch):
    observed = AgentExecution(
        task={"id": "12"}, task_id="12", customer_id="customer", fault="timeout",
        execution_id="run-id", execution_ids=("run-id",), eval_run_id="eval-id",
        before=object(), after=object(),
        error={"type": "TimeoutError", "message": "Agent timed out"},
    )
    trace = SimpleNamespace(
        trace_ids=("trace-id",), runs=(1,), errors=("transport_error",),
    )

    @dataclass
    class Score:
        score: float = 0.0
        db_checks: tuple = ()
        nl_checks: tuple = ()
        matches: tuple = ()
        extra_write_run_ids: tuple = ()
        unsupported_actions: tuple = ()

    monkeypatch.setattr(
        agent_task, "TraceNormalizer",
        lambda: SimpleNamespace(normalize_eval_run=lambda *args, **kwargs: trace),
    )
    monkeypatch.setattr(
        agent_task, "CommerceNormalizer",
        lambda: SimpleNamespace(normalize=lambda value: value),
    )
    monkeypatch.setattr(agent_task, "EvalRecord", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        agent_task, "TaskSuccessEvaluator",
        lambda: SimpleNamespace(evaluate=lambda *args: Score()),
    )
    monkeypatch.setattr(
        agent_task, "TrajectoryEvaluator",
        lambda: SimpleNamespace(evaluate=lambda *args: Score()),
    )

    row = evaluate(observed)
    assert row["scores"] == {"task_success": 0.0, "trajectory": 0.0}
    assert row["error"]["message"] == "Agent timed out"
    assert row["trace_observation"]["trace_ids"] == ["trace-id"]

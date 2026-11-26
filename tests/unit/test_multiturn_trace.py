"""An evaluation ID joins Agent runs without mixing unrelated traces."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from evals.adapter.commerce_adapter import CommerceSnapshot
from evals.adapter.trace_adapter import AgentTrace, TraceAdapter, TraceRun
from evals.evaluation.trajectory import TrajectoryEvaluator
from evals.evaluators.agent_task import evaluate_orchestration
from evals.executors.user_simulate.orchestrator import OrchestrationResult, State
from evals.normalizer.models import EvalRecord, NormalizedCommerce
from evals.normalizer.trace_normalizer import TraceNormalizer
from services.agent_service.app.execution.lifecycle import eval_run_id_from_key
from services.agent_service.app.workers.tasks import _eval_trace_metadata

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_agent_trace_metadata_correlates_task_and_round():
    eval_id = uuid4().hex
    agent_id = uuid4()
    assert eval_run_id_from_key(f"user-sim:{eval_id}:2") == eval_id
    assert eval_run_id_from_key("ordinary-run") is None
    assert _eval_trace_metadata(SimpleNamespace(eval_run_id=eval_id, run_id=agent_id)) == {
        "eval_run_id": eval_id,
        "agent_run_id": str(agent_id),
        "execution_id": str(agent_id),
    }


def test_adapter_groups_roots_by_eval_id_and_validates_agent_ids():
    eval_run_id, first_id, second_id = uuid4(), uuid4(), uuid4()

    def root(agent_id, seconds):
        run_id = uuid4()
        return SimpleNamespace(
            id=run_id, trace_id=run_id, parent_run_id=None, name="graph", run_type="chain",
            inputs={}, outputs={}, error=None, start_time=NOW + timedelta(seconds=seconds),
            end_time=NOW + timedelta(seconds=seconds + 1),
            extra={"metadata": {"eval_run_id": eval_run_id.hex,
                                "agent_run_id": str(agent_id), "execution_id": str(agent_id)}},
            total_tokens=None, total_cost=None,
        )

    roots = [root(second_id, 20), root(first_id, 0), root(first_id, 10)]

    class Client:
        def list_runs(self, **kwargs):
            if kwargs.get("is_root"):
                assert eval_run_id.hex in kwargs["filter"]
                return roots
            return [item for item in roots if str(item.id) == kwargs["trace_id"]]

    adapter = TraceAdapter(project="bench", client=Client())
    traces = adapter.wait_for_eval_traces(str(eval_run_id), [str(first_id), str(second_id)])
    assert [trace.execution_id for trace in traces] == [str(first_id), str(first_id), str(second_id)]
    assert len({trace.trace_id for trace in traces}) == 3
    with pytest.raises(ValueError, match="unexpected agent_run_id"):
        adapter.wait_for_eval_traces(str(eval_run_id), [str(first_id)], timeout=0)


def test_normalizer_and_trajectory_use_both_agent_turns():
    first_id, second_id = str(uuid4()), str(uuid4())

    def tool(agent_id, trace_id, run_id, name, args, seconds):
        return AgentTrace(agent_id, trace_id, f"root-{trace_id}", (
            TraceRun(
                run_id, None, name, "tool", {"input": args}, {"ok": True}, None,
                NOW + timedelta(seconds=seconds), NOW + timedelta(seconds=seconds + 1),
                {"tool_call_id": "same-local-id"}, None, None,
            ),
        ))

    traces = [
        tool(first_id, "trace-1", "tool-1", "get_order", {"order_id": "o1"}, 0),
        tool(second_id, "trace-2", "tool-2", "get_product", {"product_id": "p1"}, 10),
    ]

    class Adapter:
        def wait_for_eval_traces(self, eval_id, agent_ids, *, timeout):
            assert (eval_id, agent_ids) == ("eval-1", [first_id, second_id])
            return traces

    normalized = TraceNormalizer().normalize_eval_run(
        "eval-1", [first_id, second_id], adapter=Adapter(),
    )
    commerce = NormalizedCommerce("task-1", "u1", NOW, {}, {}, {}, {}, {}, {}, {})
    record = EvalRecord(
        "task-1", "u1", first_id, normalized, commerce,
        eval_run_id="eval-1", agent_run_ids=(first_id, second_id),
    )
    task = {"id": "task-1", "evaluation_criteria": {"actions": [
        {"action_id": "a1", "name": "get_order_details", "arguments": {"order_id": "o1"}},
        {"action_id": "a2", "name": "get_product_details", "arguments": {"product_id": "p1"}},
    ]}}
    scored = TrajectoryEvaluator().evaluate(record, task)
    assert scored.score == 1.0
    assert [match.matched_run_id for match in scored.matches] == ["tool-1", "tool-2"]
    assert normalized.agent_run_ids == (first_id, second_id)


def test_evaluation_entry_uses_full_dialogue_and_all_agent_runs():
    first_id, second_id = str(uuid4()), str(uuid4())
    result = OrchestrationResult(
        "task-1", "u1", "eval-1", State.STOPPED,
        execution_ids=[first_id, second_id],
        history=[("user", "Help"), ("agent", "A refund is available."),
                 ("user", "Thanks"), ("agent", "Goodbye")],
    )
    snapshot = CommerceSnapshot("task-1", "u1", NOW, {"user_id": "u1"}, [], [], {}, {}, {}, {})
    task = {"id": "task-1", "evaluation_criteria": {
        "actions": [], "communicate_info": ["refund"],
    }}

    class Adapter:
        def wait_for_eval_traces(self, eval_id, agent_ids, *, timeout):
            assert (eval_id, agent_ids) == ("eval-1", [first_id, second_id])
            return [
                AgentTrace(first_id, "trace-1", "root-1", ()),
                AgentTrace(second_id, "trace-2", "root-2", ()),
            ]

    row = evaluate_orchestration(result, task, snapshot, snapshot, customer_id="u1", trace_adapter=Adapter())
    assert row["error"] is None
    assert row["scores"] == {"task_success": 1.0, "trajectory": 1.0}
    assert "database_restored" not in row
    assert row["execution_ids"] == [first_id, second_id]
    assert row["history"] == result.history

"""Score Agent task observations with the existing outcome and trace graders."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from evals.adapter.commerce_adapter import CommerceSnapshot
from evals.adapter.trace_adapter import AgentTrace, TraceAdapter
from evals.evaluation.task_success import TaskSuccessEvaluator
from evals.evaluation.trajectory import TrajectoryEvaluator
from evals.executors.user_simulate.orchestrator import OrchestrationResult, State
from evals.normalizer.commerce_normalizer import CommerceNormalizer
from evals.normalizer.models import EvalRecord
from evals.normalizer.trace_normalizer import TraceNormalizer


@dataclass
class AgentExecution:
    """One task's observations before any scoring or normalization."""

    task: dict[str, Any]
    task_id: str
    customer_id: str | None = None
    execution_id: str | None = None
    status: str = "ERROR"
    before: CommerceSnapshot | None = None
    after: CommerceSnapshot | None = None
    traces: list[AgentTrace] | None = None
    error: dict[str, str] | None = None
    eval_run_id: str | None = None
    execution_ids: tuple[str, ...] = ()
    history: tuple[tuple[str, str], ...] = ()
    fault: str | None = None
    fault_target: str | None = None
    fault_ms: int | None = None
    proxy_observation: dict[str, Any] | None = None
    tool_execution_observation: dict[str, Any] | None = None


def reasons(success: Any, trajectory: Any) -> list[str]:
    details = [check.detail for check in (*success.db_checks, *success.nl_checks) if not check.passed]
    details.extend(
        f"missing action {match.action_id}" for match in trajectory.matches
        if match.matched_run_id is None
    )
    details.extend(f"unexpected write {run_id}" for run_id in trajectory.extra_write_run_ids)
    details.extend(f"unsupported action {action_id}" for action_id in trajectory.unsupported_actions)
    return details or ["all evaluated checks passed"]


def evaluate(
    observed: AgentExecution, *, trace_adapter: TraceAdapter | None = None,
    trace_timeout: float = 30.0,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "task_id": observed.task_id,
        "customer_id": observed.customer_id,
        "execution_id": observed.execution_id,
        "eval_run_id": observed.eval_run_id,
        "execution_ids": list(observed.execution_ids),
        "history": list(observed.history),
        "status": observed.status,
        "scores": None,
        "reasons": [],
        "error": observed.error,
        "fault": observed.fault,
        "fault_target": observed.fault_target,
        "fault_ms": observed.fault_ms,
        "proxy_observation": observed.proxy_observation,
        "tool_execution_observation": observed.tool_execution_observation,
        "trace_observation": None,
    }
    if observed.error:
        row["reasons"] = [observed.error["message"]]
        if observed.fault is not None and not observed.execution_ids:
            row["trace_observation"] = {"unavailable_reason": "no_execution_ids"}
        can_score_fault = (
            observed.fault is not None and observed.execution_id is not None and bool(observed.execution_ids)
            and observed.before is not None and observed.after is not None
        )
        if not can_score_fault and observed.fault is not None and observed.eval_run_id and observed.execution_ids:
            try:
                adapter = trace_adapter or TraceAdapter()
                traces = adapter.wait_for_eval_traces(
                    observed.eval_run_id, list(observed.execution_ids), timeout=trace_timeout,
                )
                row["trace_observation"] = {
                    "trace_ids": [trace.trace_id for trace in traces],
                    "run_count": sum(len(trace.runs) for trace in traces),
                    "errors": [run.error for trace in traces for run in trace.runs if run.error],
                }
            except Exception as exc:  # noqa: BLE001 - retain the primary task error
                row["trace_observation"] = {"error_type": type(exc).__name__, "message": str(exc)}
        if not can_score_fault:
            return row
    try:
        if not observed.customer_id or not observed.execution_id:
            raise ValueError("Agent execution is missing identity or execution ID")
        if observed.before is None or observed.after is None:
            raise ValueError("Agent execution is missing Commerce snapshots")
        if observed.eval_run_id:
            trace = TraceNormalizer().normalize_eval_run(
                observed.eval_run_id, observed.execution_ids,
                adapter=trace_adapter, timeout=trace_timeout,
            )
        else:
            if not observed.traces:
                raise ValueError("Agent execution is missing traces")
            trace = TraceNormalizer().normalize(observed.traces)
        row["trace_observation"] = {
            "trace_ids": list(trace.trace_ids),
            "run_count": len(trace.runs),
            "errors": list(trace.errors),
        }
        normalizer = CommerceNormalizer()
        record = EvalRecord(
            task_id=observed.task_id,
            customer_id=observed.customer_id,
            execution_id=observed.execution_id,
            trace=trace,
            commerce=normalizer.normalize(observed.after),
            commerce_before=normalizer.normalize(observed.before),
            eval_run_id=observed.eval_run_id,
            agent_run_ids=observed.execution_ids,
            history=observed.history,
        )
        success = TaskSuccessEvaluator().evaluate(record, observed.task)
        trajectory = TrajectoryEvaluator().evaluate(record, observed.task)
        row["scores"] = {"task_success": success.score, "trajectory": trajectory.score}
        row["reasons"] = (
            ([observed.error["message"]] if observed.error else [])
            + reasons(success, trajectory)
        )
        row["details"] = {"task_success": asdict(success), "trajectory": asdict(trajectory)}
    except Exception as exc:  # noqa: BLE001 - one bad result must not discard the batch
        if observed.error:
            row["evaluation_error"] = {"type": type(exc).__name__, "message": str(exc)}
            row["reasons"].append(str(exc))
        else:
            row["error"] = {"type": type(exc).__name__, "message": str(exc)}
            row["reasons"] = [str(exc)]
        if observed.fault is not None and row["trace_observation"] is None:
            row["trace_observation"] = {"error_type": type(exc).__name__, "message": str(exc)}
    return row


def evaluate_orchestration(
    result: OrchestrationResult, task: dict[str, Any],
    before: CommerceSnapshot | None, after: CommerceSnapshot | None,
    *, customer_id: str, trace_adapter: TraceAdapter | None = None, trace_timeout: float = 30.0,
) -> dict[str, Any]:
    """Score one full simulated-user task after the runner captures Commerce state."""
    observed = AgentExecution(
        task=task, task_id=result.task_id, customer_id=customer_id,
        execution_id=result.execution_ids[0] if result.execution_ids else None,
        status=result.state.value, before=before, after=after,
        eval_run_id=result.eval_run_id,
        execution_ids=tuple(result.execution_ids), history=tuple(result.history),
        error=(
            {"type": result.state.value, "message": "; ".join(result.errors)}
            if result.state in {State.ERROR, State.TIMEOUT} else None
        ),
    )
    return evaluate(observed, trace_adapter=trace_adapter, trace_timeout=trace_timeout)

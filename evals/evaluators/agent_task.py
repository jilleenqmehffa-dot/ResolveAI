"""Score Agent task observations with the existing outcome and trace graders."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from evals.evaluation.task_success import TaskSuccessEvaluator
from evals.evaluation.trajectory import TrajectoryEvaluator
from evals.executors.agent_task import AgentExecution
from evals.normalizer.commerce_normalizer import CommerceNormalizer
from evals.normalizer.models import EvalRecord
from evals.normalizer.trace_normalizer import TraceNormalizer


def reasons(success: Any, trajectory: Any) -> list[str]:
    details = [check.detail for check in (*success.db_checks, *success.nl_checks) if not check.passed]
    details.extend(
        f"missing action {match.action_id}" for match in trajectory.matches
        if match.matched_run_id is None
    )
    details.extend(f"unexpected write {run_id}" for run_id in trajectory.extra_write_run_ids)
    details.extend(f"unsupported action {action_id}" for action_id in trajectory.unsupported_actions)
    return details or ["all evaluated checks passed"]


def evaluate(observed: AgentExecution) -> dict[str, Any]:
    row: dict[str, Any] = {
        "task_id": observed.task_id,
        "customer_id": observed.customer_id,
        "execution_id": observed.execution_id,
        "status": observed.status,
        "scores": None,
        "reasons": [],
        "error": observed.error,
    }
    if observed.error:
        row["reasons"] = [observed.error["message"]]
        return row
    try:
        if not observed.customer_id or not observed.execution_id:
            raise ValueError("Agent execution is missing identity or execution ID")
        if observed.before is None or observed.after is None or not observed.traces:
            raise ValueError("Agent execution is missing snapshots or traces")
        normalizer = CommerceNormalizer()
        record = EvalRecord(
            task_id=observed.task_id,
            customer_id=observed.customer_id,
            execution_id=observed.execution_id,
            trace=TraceNormalizer().normalize(observed.traces),
            commerce=normalizer.normalize(observed.after),
            commerce_before=normalizer.normalize(observed.before),
        )
        success = TaskSuccessEvaluator().evaluate(record, observed.task)
        trajectory = TrajectoryEvaluator().evaluate(record, observed.task)
        row["scores"] = {"task_success": success.score, "trajectory": trajectory.score}
        row["reasons"] = reasons(success, trajectory)
        row["details"] = {"task_success": asdict(success), "trajectory": asdict(trajectory)}
    except Exception as exc:  # noqa: BLE001 - one bad result must not discard the batch
        row["error"] = {"type": type(exc).__name__, "message": str(exc)}
        row["reasons"] = [str(exc)]
    return row

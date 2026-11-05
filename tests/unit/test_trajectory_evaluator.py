"""Trajectory scoring matches supported actions and reports capability gaps."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.evaluation.trajectory import TrajectoryEvaluator
from evals.normalizer.models import (
    EvalRecord,
    NormalizedCommerce,
    NormalizedToolCall,
    NormalizedTrace,
)


def record(calls: tuple[NormalizedToolCall, ...]) -> EvalRecord:
    trace = NormalizedTrace("ex", (), None, calls, (), ())
    commerce = NormalizedCommerce(
        "1", "u1", datetime(2026, 1, 1, tzinfo=UTC), {}, {}, {}, {}, {}, {}, {}
    )
    return EvalRecord("1", "u1", "ex", trace, commerce)


def call(
    run_id: str, name: str, args: dict, *, error=None, call_id=None
) -> NormalizedToolCall:
    return NormalizedToolCall(
        run_id, "trace", call_id, name, args, {}, {}, error, {}, {}
    )


def test_supported_actions_match_semantically_and_unsupported_are_reported() -> None:
    task = {
        "id": "1",
        "evaluation_criteria": {
            "actions": [
                {
                    "action_id": "a1",
                    "name": "find_user_id_by_name_zip",
                    "arguments": {},
                },
                {
                    "action_id": "a2",
                    "name": "get_order_details",
                    "arguments": {"order_id": "o1"},
                },
                {
                    "action_id": "a3",
                    "name": "cancel_pending_order",
                    "arguments": {"order_id": "o1", "reason": "done"},
                },
            ]
        },
    }
    observed = record(
        (
            call("r1", "get_order", {"order_id": "o1"}),
            call("r2", "cancel_pending_order", {"order_id": "o1", "reason": "done"}),
        )
    )
    result = TrajectoryEvaluator().evaluate(observed, task)
    assert result.score == 1.0
    assert result.supported_actions == 2
    assert result.unsupported_actions == ("a1",)
    assert [match.matched_run_id for match in result.matches] == ["r1", "r2"]


def test_missing_action_and_extra_write_reduce_score() -> None:
    task = {
        "id": "1",
        "evaluation_criteria": {
            "actions": [
                {
                    "action_id": "a1",
                    "name": "cancel_pending_order",
                    "arguments": {"order_id": "o1", "reason": "done"},
                },
            ]
        },
    }
    observed = record(
        (call("r1", "cancel_pending_order", {"order_id": "o2", "reason": "done"}),)
    )
    result = TrajectoryEvaluator().evaluate(observed, task)
    assert result.score == 0.0
    assert result.matches[0].matched_run_id is None
    assert result.extra_write_run_ids == ("r1",)


def test_failed_retry_with_same_call_id_uses_successful_attempt() -> None:
    task = {
        "id": "1",
        "evaluation_criteria": {
            "actions": [
                {
                    "action_id": "a1",
                    "name": "cancel_pending_order",
                    "arguments": {"order_id": "o1", "reason": "done"},
                },
            ]
        },
    }
    args = {"order_id": "o1", "reason": "done"}
    observed = record(
        (
            call(
                "r1",
                "cancel_pending_order",
                args,
                error={"code": "transport_error"},
                call_id="c1",
            ),
            call("r2", "cancel_pending_order", args, call_id="c1"),
        )
    )
    result = TrajectoryEvaluator().evaluate(observed, task)
    assert result.score == 1.0
    assert result.extra_write_run_ids == ()
    assert result.matches[0].matched_run_id == "r2"


def test_expected_missing_read_requires_matching_not_found_result() -> None:
    task = {
        "id": "1",
        "evaluation_criteria": {
            "actions": [
                {
                    "action_id": "a1",
                    "name": "get_order_details",
                    "arguments": {"order_id": "bad"},
                    "expected_outcome": "not_found",
                },
                {
                    "action_id": "a2",
                    "name": "get_order_details",
                    "arguments": {"order_id": "good"},
                },
            ]
        },
    }
    observed = record(
        (
            call("r1", "get_order", {"order_id": "bad"}, error={"code": "not_found"}),
            call("r2", "get_order", {"order_id": "good"}),
        )
    )
    result = TrajectoryEvaluator().evaluate(observed, task)
    assert result.score == 1.0
    assert [match.matched_run_id for match in result.matches] == ["r1", "r2"]
    assert result.failed_run_ids == ()

    wrong_outcome = record(
        (
            call(
                "r1",
                "get_order",
                {"order_id": "bad"},
                error={"code": "transport_error"},
            ),
        )
    )
    result = TrajectoryEvaluator().evaluate(wrong_outcome, task)
    assert result.score == 0.0
    assert result.failed_run_ids == ("r1",)

    textual_404 = record(
        (call("r3", "get_order", {"order_id": "bad"}, error="HTTP 404 Not Found"),)
    )
    assert (
        TrajectoryEvaluator().evaluate(textual_404, task).matches[0].matched_run_id
        == "r3"
    )


def test_missing_read_is_not_credited_without_explicit_expectation() -> None:
    task = {
        "id": "1",
        "evaluation_criteria": {
            "actions": [
                {
                    "action_id": "a1",
                    "name": "get_product_details",
                    "arguments": {"product_id": "missing"},
                }
            ]
        },
    }
    observed = record(
        (
            call(
                "r1",
                "get_product",
                {"product_id": "missing"},
                error={"code": "not_found"},
            ),
        )
    )
    assert TrajectoryEvaluator().evaluate(observed, task).score == 0.0

    task["evaluation_criteria"]["actions"][0]["expected_outcome"] = "not_found"
    assert TrajectoryEvaluator().evaluate(observed, task).score == 1.0


def test_not_found_cannot_be_expected_for_write_actions() -> None:
    task = {
        "id": "1",
        "evaluation_criteria": {
            "actions": [
                {
                    "action_id": "a1",
                    "name": "cancel_pending_order",
                    "arguments": {"order_id": "bad", "reason": "done"},
                    "expected_outcome": "not_found",
                }
            ]
        },
    }
    with pytest.raises(ValueError, match="not_found is not a valid outcome"):
        TrajectoryEvaluator().evaluate(record(()), task)


def test_marked_benchmark_lookups_reference_missing_resources() -> None:
    datasets = Path(__file__).resolve().parents[2] / "evals/datasets"
    database = json.loads((datasets / "db.json").read_text(encoding="utf-8"))
    tasks = json.loads((datasets / "tasks.json").read_text(encoding="utf-8"))
    marked = [
        action
        for task in tasks
        for action in task["evaluation_criteria"]["actions"]
        if action.get("expected_outcome") == "not_found"
    ]
    assert len(marked) == 7
    for action in marked:
        if action["name"] == "get_order_details":
            assert action["arguments"]["order_id"] not in database["orders"]
        elif action["name"] == "get_product_details":
            assert action["arguments"]["product_id"] not in database["products"]
        else:
            pytest.fail(f"Unexpected not_found action: {action['name']}")

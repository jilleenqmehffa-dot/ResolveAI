"""Compare observed Agent tool calls with supported Bench action expectations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from evals.evaluation.task_success import WRITE_ACTIONS
from evals.normalizer.models import EvalRecord, NormalizedToolCall

TOOL_NAMES = {
    "get_order_details": "get_order",
    "get_product_details": "get_product",
    "get_item_details": "get_item",
    "get_user_details": "get_customer",
    **{name: name for name in WRITE_ACTIONS},
}
MISSING_RESOURCE_READS = frozenset(
    {"get_order_details", "get_product_details", "get_item_details"}
)


@dataclass(frozen=True)
class ActionMatch:
    action_id: str
    expected_tool: str
    matched_run_id: str | None


@dataclass(frozen=True)
class TrajectoryResult:
    task_id: str
    score: float
    supported_actions: int
    matches: tuple[ActionMatch, ...]
    unsupported_actions: tuple[str, ...]
    extra_write_run_ids: tuple[str, ...]
    failed_run_ids: tuple[str, ...]


def _expected_args(name: str, args: dict[str, Any], customer_id: str) -> dict[str, Any]:
    if name in {"get_user_details", "modify_user_address"}:
        if str(args["user_id"]) != customer_id:
            return {"_wrong_customer": args["user_id"]}
        return {key: value for key, value in args.items() if key != "user_id"}
    return args


def _same_args(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if set(expected) != set(actual):
        return False
    for key, value in expected.items():
        observed = actual[key]
        if isinstance(value, list) and isinstance(observed, list):
            if [str(item) for item in value] != [str(item) for item in observed]:
                return False
        elif str(value) != str(observed):
            return False
    return True


def _is_not_found(error: Any) -> bool:
    if isinstance(error, dict):
        return error.get("code") == "not_found"
    if isinstance(error, str):
        return bool(re.search(r"\b(?:not_found|not found|404)\b", error, re.IGNORECASE))
    return False


def _logical_calls(calls: tuple[NormalizedToolCall, ...]) -> list[NormalizedToolCall]:
    """Collapse repeated trace runs for one durable tool call ID."""
    grouped: dict[str, NormalizedToolCall] = {}
    for call in calls:
        key = call.tool_call_id or call.run_id
        previous = grouped.get(key)
        if previous is None or previous.error is not None and call.error is None:
            grouped[key] = call
    return list(grouped.values())


class TrajectoryEvaluator:
    """Match supported action outcomes and penalize unexpected write attempts."""

    def evaluate(self, record: EvalRecord, task: dict[str, Any]) -> TrajectoryResult:
        if str(task["id"]) != record.task_id:
            raise ValueError("Task ID does not match EvalRecord")
        calls = _logical_calls(record.trace.tool_calls)
        used: set[str] = set()
        matches: list[ActionMatch] = []
        unsupported: list[str] = []
        expected_missing_runs: set[str] = set()
        for action in task["evaluation_criteria"]["actions"]:
            name = action["name"]
            outcome = action.get("expected_outcome", "success")
            if outcome not in {"success", "not_found"}:
                raise ValueError(f"Unsupported expected outcome: {outcome}")
            if outcome == "not_found" and name not in MISSING_RESOURCE_READS:
                raise ValueError(f"not_found is not a valid outcome for {name}")
            tool_name = TOOL_NAMES.get(name)
            if tool_name is None:
                unsupported.append(action["action_id"])
                continue
            args = _expected_args(name, action["arguments"], record.customer_id)
            matched = next(
                (
                    call
                    for call in calls
                    if call.run_id not in used
                    and call.name == tool_name
                    and (
                        call.error is None
                        if outcome == "success"
                        else _is_not_found(call.error)
                    )
                    and _same_args(args, call.arguments)
                ),
                None,
            )
            if matched is not None:
                used.add(matched.run_id)
                if outcome == "not_found":
                    expected_missing_runs.add(matched.run_id)
            matches.append(
                ActionMatch(
                    action["action_id"], tool_name, matched.run_id if matched else None
                )
            )

        expected_writes = [
            (
                TOOL_NAMES[action["name"]],
                _expected_args(action["name"], action["arguments"], record.customer_id),
            )
            for action in task["evaluation_criteria"]["actions"]
            if action["name"] in WRITE_ACTIONS
        ]
        extra_writes = tuple(
            call.run_id
            for call in calls
            if call.name in WRITE_ACTIONS
            and call.run_id not in used
            and not (
                call.error is not None
                and any(
                    call.name == name and _same_args(args, call.arguments)
                    for name, args in expected_writes
                )
            )
        )
        failed = tuple(
            call.run_id
            for call in calls
            if call.error is not None and call.run_id not in expected_missing_runs
        )
        matched_count = sum(match.matched_run_id is not None for match in matches)
        denominator = len(matches) + len(extra_writes)
        score = matched_count / denominator if denominator else 1.0
        return TrajectoryResult(
            task_id=record.task_id,
            score=score,
            supported_actions=len(matches),
            matches=tuple(matches),
            unsupported_actions=tuple(unsupported),
            extra_write_run_ids=extra_writes,
            failed_run_ids=failed,
        )

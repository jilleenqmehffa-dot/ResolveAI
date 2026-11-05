"""Common evaluator input types; none of these types assigns a score."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class NormalizedToolCall:
    run_id: str
    trace_id: str
    tool_call_id: str | None
    name: str
    arguments: dict[str, Any]
    result: Any
    business_result: Any
    error: Any
    raw_inputs: dict[str, Any]
    raw_outputs: dict[str, Any] | None


@dataclass(frozen=True)
class GuardDecision:
    run_id: str
    decision: str
    tool_call_id: str | None
    reason: str | None


@dataclass(frozen=True)
class NormalizedRun:
    id: str
    trace_id: str
    root_run_id: str
    parent_run_id: str | None
    name: str
    run_type: str
    inputs: Any
    outputs: Any
    error: str | None
    start_time: datetime
    end_time: datetime | None
    metadata: dict[str, Any]
    total_tokens: int | None
    total_cost: float | None
    raw_inputs: dict[str, Any]
    raw_outputs: dict[str, Any] | None


@dataclass(frozen=True)
class NormalizedTrace:
    execution_id: str
    trace_ids: tuple[str, ...]
    final_answer: str | None
    tool_calls: tuple[NormalizedToolCall, ...]
    guard_decisions: tuple[GuardDecision, ...]
    errors: tuple[str, ...]
    root_run_ids: tuple[str, ...] = ()
    runs: tuple[NormalizedRun, ...] = ()


@dataclass(frozen=True)
class NormalizedCommerce:
    task_id: str
    customer_id: str
    captured_at: datetime
    customer: dict[str, Any]
    payment_methods: dict[str, dict[str, Any]]
    order_summaries: dict[str, dict[str, Any]]
    orders: dict[str, dict[str, Any] | None]
    products: dict[str, dict[str, Any] | None]
    items: dict[str, dict[str, Any] | None]
    identity_lookups: dict[str, str | None]


@dataclass(frozen=True)
class EvalRecord:
    """Joined normalized observations for a later evaluator to compare."""

    task_id: str
    customer_id: str
    execution_id: str
    trace: NormalizedTrace
    commerce: NormalizedCommerce
    commerce_before: NormalizedCommerce | None = None

    def __post_init__(self) -> None:
        if self.execution_id != self.trace.execution_id:
            raise ValueError("Trace execution ID does not match EvalRecord")
        if self.task_id != self.commerce.task_id:
            raise ValueError("Commerce task ID does not match EvalRecord")
        if self.customer_id != self.commerce.customer_id:
            raise ValueError("Commerce customer ID does not match EvalRecord")
        if self.commerce_before is not None:
            if self.task_id != self.commerce_before.task_id:
                raise ValueError("Baseline task ID does not match EvalRecord")
            if self.customer_id != self.commerce_before.customer_id:
                raise ValueError("Baseline customer ID does not match EvalRecord")

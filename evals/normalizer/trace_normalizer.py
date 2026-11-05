"""Normalize LangSmith Agent traces without fetching data or scoring them."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from evals.adapter.trace_adapter import AgentTrace, TraceRun
from evals.normalizer.common import (
    decode_json,
    normalize_after_sales_case,
    normalize_customer,
    normalize_item,
    normalize_order,
    normalize_product,
)
from evals.normalizer.models import (
    GuardDecision,
    NormalizedRun,
    NormalizedToolCall,
    NormalizedTrace,
)


def _unwrap(value: Any) -> Any:
    """Remove transport wrappers around a tool response, retaining its data."""
    value = decode_json(value)
    while isinstance(value, dict):
        if "ok" in value and value["ok"] is False:
            return value
        if "output" in value and len(value) == 1:
            value = decode_json(value["output"])
        elif "data" in value and set(value) <= {"ok", "data"}:
            value = decode_json(value["data"])
        else:
            break
    return value


def _arguments(inputs: dict[str, Any]) -> dict[str, Any]:
    for key in ("input", "args"):
        value = decode_json(inputs.get(key))
        if isinstance(value, dict):
            return _normalize_arguments(value)
    return _normalize_arguments(inputs)


def _normalize_arguments(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    if "user_id" in result:
        user_id = result.pop("user_id")
        result["customer_id"] = str(user_id) if user_id is not None else None
    for key in (
        "order_id",
        "customer_id",
        "product_id",
        "item_id",
        "case_id",
        "payment_method_id",
    ):
        if key in result and result[key] is not None:
            result[key] = str(result[key])
    for key in ("item_ids", "new_item_ids"):
        if isinstance(result.get(key), list):
            result[key] = [str(item) for item in result[key]]
    return result


def _business_result(name: str, result: Any, args: dict[str, Any]) -> Any:
    if name == "get_order" and isinstance(result, dict):
        return normalize_order(result, order_id=args.get("order_id"))
    if name == "list_orders" and isinstance(result, list):
        return [normalize_order(order) for order in result]
    if name == "get_customer" and isinstance(result, dict):
        return normalize_customer(result)
    if name == "get_product" and isinstance(result, dict):
        return normalize_product(result)
    if name == "list_products" and isinstance(result, list):
        return [normalize_product(product) for product in result]
    if name == "get_item" and isinstance(result, dict):
        return normalize_item(result)
    if name in {"get_after_sales_case", "mark_after_sales_refunded"} and isinstance(
        result, dict
    ):
        normalized = normalize_after_sales_case(result)
        if "case_id" in args:
            normalized.setdefault("case_id", args["case_id"])
        return normalized
    if name == "list_after_sales_cases" and isinstance(result, list):
        return [normalize_after_sales_case(case) for case in result]
    return result


def _answer(value: Any) -> str | None:
    value = decode_json(value)
    if not isinstance(value, dict):
        return None
    messages = value.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            kind = message.get("type") or message.get("role")
            content = message.get("content")
            if (
                kind in (None, "ai", "AIMessage", "assistant")
                and isinstance(content, str)
                and content.strip()
                and not message.get("tool_calls")
            ):
                return content
    for key in ("output", "result", "response"):
        nested = value.get(key)
        if isinstance(nested, str) and nested.strip() and key == "output":
            return nested
        found = _answer(nested)
        if found is not None:
            return found
    return None


def _tool_call(trace_id: str, run: TraceRun) -> NormalizedToolCall:
    args = _arguments(run.inputs)
    result = _unwrap(run.outputs)
    error: Any = run.error
    if isinstance(result, dict) and result.get("ok") is False:
        error = result.get("error") or error
        result = None
    elif isinstance(result, dict) and "error" in result and len(result) == 1:
        error = result["error"]
        result = None
    return NormalizedToolCall(
        run_id=run.id,
        trace_id=trace_id,
        tool_call_id=run.metadata.get("tool_call_id") or args.get("tool_call_id"),
        name=run.name,
        arguments=args,
        result=result,
        business_result=_business_result(run.name, result, args),
        error=error,
        raw_inputs=run.inputs,
        raw_outputs=run.outputs,
    )


def _guard(run: TraceRun) -> GuardDecision | None:
    output = _unwrap(run.outputs)
    if not isinstance(output, dict):
        return None
    result = output.get("guard_result", output)
    if not isinstance(result, dict) or "decision" not in result:
        return None
    return GuardDecision(
        run_id=run.id,
        decision=str(result["decision"]),
        tool_call_id=result.get("tool_call_id"),
        reason=result.get("reason"),
    )


class TraceNormalizer:
    def normalize(self, traces: Sequence[AgentTrace]) -> NormalizedTrace:
        if not traces:
            raise ValueError("At least one Agent trace is required")
        execution_id = traces[0].execution_id
        if any(trace.execution_id != execution_id for trace in traces):
            raise ValueError("Traces belong to different Agent executions")

        tool_calls: list[tuple[Any, NormalizedToolCall]] = []
        decisions: list[tuple[Any, GuardDecision]] = []
        errors: list[str] = []
        answers: list[tuple[Any, str]] = []
        runs: list[NormalizedRun] = []
        for trace in traces:
            for run in trace.runs:
                runs.append(
                    NormalizedRun(
                        id=run.id,
                        trace_id=trace.trace_id,
                        root_run_id=trace.root_run_id,
                        parent_run_id=run.parent_run_id,
                        name=run.name,
                        run_type=run.run_type,
                        inputs=_arguments(run.inputs),
                        outputs=_unwrap(run.outputs),
                        error=run.error,
                        start_time=run.start_time,
                        end_time=run.end_time,
                        metadata=dict(run.metadata),
                        total_tokens=run.total_tokens,
                        total_cost=run.total_cost,
                        raw_inputs=run.inputs,
                        raw_outputs=run.outputs,
                    )
                )
                if run.run_type == "tool":
                    tool_call = _tool_call(trace.trace_id, run)
                    tool_calls.append((run.start_time, tool_call))
                    if tool_call.error is not None and not run.error:
                        errors.append(str(tool_call.error))
                if run.name == "tool_guard":
                    decision = _guard(run)
                    if decision is not None:
                        decisions.append((run.start_time, decision))
                if run.error:
                    errors.append(run.error)
                if run.id == trace.root_run_id or run.name == "agent":
                    answer = _answer(run.outputs)
                    if answer is not None:
                        answers.append((run.end_time or run.start_time, answer))

        tool_calls.sort(key=lambda item: item[0])
        decisions.sort(key=lambda item: item[0])
        answers.sort(key=lambda item: item[0])
        runs.sort(key=lambda run: (run.start_time, run.id))
        return NormalizedTrace(
            execution_id=execution_id,
            trace_ids=tuple(trace.trace_id for trace in traces),
            final_answer=answers[-1][1] if answers else None,
            tool_calls=tuple(call for _, call in tool_calls),
            guard_decisions=tuple(decision for _, decision in decisions),
            errors=tuple(errors),
            root_run_ids=tuple(trace.root_run_id for trace in traces),
            runs=tuple(runs),
        )

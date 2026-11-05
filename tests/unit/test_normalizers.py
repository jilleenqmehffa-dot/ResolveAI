"""Trace and Commerce facts share a vocabulary without being scored here."""

from datetime import UTC, datetime

import pytest

from evals.adapter.commerce_adapter import CommerceSnapshot
from evals.adapter.trace_adapter import AgentTrace, TraceRun
from evals.normalizer.commerce_normalizer import CommerceNormalizer
from evals.normalizer.common import normalize_customer, normalize_order
from evals.normalizer.models import EvalRecord
from evals.normalizer.trace_normalizer import TraceNormalizer

NOW = datetime(2026, 11, 2, tzinfo=UTC)


def test_customer_order_sequence_and_methods_are_normalized() -> None:
    customer = normalize_customer(
        {
            "user_id": "u1",
            "name": {"first_name": "Ada", "last_name": "Lin"},
            "orders": ["older", "newer"],
            "payment_methods": {
                "card1": {
                    "id": "card1",
                    "source": "credit_card",
                    "balance": "5.0",
                }
            },
        }
    )
    assert customer["orders"] == ["older", "newer"]
    assert customer["payment_methods"][0]["balance"] == "5.00"
    assert normalize_order(
        {"order_id": "newer", "status": "pending", "order_position": 1}
    )["order_position"] == 1


def trace_run(
    run_id: str,
    name: str,
    *,
    run_type: str = "chain",
    inputs: dict | None = None,
    outputs: dict | None = None,
    error: str | None = None,
) -> TraceRun:
    return TraceRun(
        id=run_id,
        parent_run_id=None,
        name=name,
        run_type=run_type,
        inputs=inputs or {},
        outputs=outputs,
        error=error,
        start_time=NOW,
        end_time=NOW,
        metadata={},
        total_tokens=None,
        total_cost=None,
    )


def test_customer_tool_trace_keeps_order_sequence() -> None:
    trace = AgentTrace(
        execution_id="execution-1",
        trace_id="trace-1",
        root_run_id="root-1",
        runs=(
            trace_run("root-1", "graph"),
            trace_run(
                "tool-1",
                "get_customer",
                run_type="tool",
                outputs={
                    "output": {
                        "user_id": "u1",
                        "orders": ["older", "newer"],
                        "payment_methods": [],
                    }
                },
            ),
        ),
    )
    result = TraceNormalizer().normalize([trace]).tool_calls[0].business_result
    assert result["orders"] == ["older", "newer"]


def test_order_status_is_identical_after_normalizing_trace_and_commerce() -> None:
    trace = AgentTrace(
        execution_id="execution-1",
        trace_id="trace-1",
        root_run_id="root-1",
        runs=(
            trace_run(
                "root-1",
                "graph",
                outputs={
                    "messages": [
                        {"type": "human", "content": "Where is my order?"},
                        {"type": "ai", "content": "Your order has shipped."},
                    ]
                },
            ),
            trace_run(
                "tool-1",
                "get_order",
                run_type="tool",
                inputs={"order_id": "123"},
                outputs={"output": '{"ok": true, "data": {"status": "shipped"}}'},
            ),
            trace_run(
                "guard-1",
                "tool_guard",
                outputs={
                    "guard_result": {
                        "decision": "allowed",
                        "tool_call_id": "call-1",
                        "reason": None,
                    }
                },
            ),
        ),
    )
    snapshot = CommerceSnapshot(
        task_id="17",
        customer_id="u1",
        captured_at=NOW,
        customer={"user_id": "u1", "first_name": "Ada", "last_name": "Lin"},
        payment_methods=[{"payment_method_id": "card1", "balance": "5.0"}],
        order_summaries=[{"id": "123", "order_status": "shipped"}],
        orders={"123": {"id": "123", "order_status": "shipped"}},
        products={},
        items={},
        identity_lookups={},
    )

    actual = TraceNormalizer().normalize([trace])
    commerce = CommerceNormalizer().normalize(snapshot)
    record = EvalRecord("17", "u1", "execution-1", actual, commerce)

    assert record.trace.tool_calls[0].business_result == {
        "order_id": "123",
        "order_status": "shipped",
    }
    assert record.commerce.orders["123"] == {
        "order_id": "123",
        "order_status": "shipped",
    }
    assert record.trace.final_answer == "Your order has shipped."
    assert record.trace.guard_decisions[0].decision == "allowed"
    assert record.commerce.payment_methods["card1"]["balance"] == "5.00"


def test_failed_tool_and_missing_business_resource_are_preserved() -> None:
    trace = AgentTrace(
        execution_id="execution-1",
        trace_id="trace-1",
        root_run_id="root-1",
        runs=(
            trace_run("root-1", "graph"),
            trace_run(
                "tool-1",
                "get_order",
                run_type="tool",
                inputs={"order_id": "missing"},
                outputs={"output": '{"ok": false, "error": {"code": "not_found"}}'},
            ),
        ),
    )
    snapshot = CommerceSnapshot(
        task_id="17",
        customer_id="u1",
        captured_at=NOW,
        customer={"user_id": "u1"},
        payment_methods=[],
        order_summaries=[],
        orders={"missing": None},
        products={"missing": None},
        items={},
        identity_lookups={},
    )

    actual = TraceNormalizer().normalize([trace])
    commerce = CommerceNormalizer().normalize(snapshot)

    assert actual.tool_calls[0].business_result is None
    assert actual.tool_calls[0].error == {"code": "not_found"}
    assert commerce.orders["missing"] is None
    assert commerce.products["missing"] is None


def test_trace_normalizer_rejects_mixed_executions() -> None:
    first = AgentTrace("one", "trace-1", "root-1", ())
    second = AgentTrace("two", "trace-2", "root-2", ())
    with pytest.raises(ValueError, match="different Agent executions"):
        TraceNormalizer().normalize([first, second])


def test_eval_record_rejects_mismatched_sources() -> None:
    trace = TraceNormalizer().normalize(
        [AgentTrace("execution-1", "trace-1", "root-1", ())]
    )
    commerce = CommerceNormalizer().normalize(
        CommerceSnapshot(
            "task-1",
            "customer-1",
            NOW,
            {"user_id": "customer-1"},
            [],
            [],
            {},
            {},
            {},
            {},
        )
    )
    with pytest.raises(ValueError, match="Trace execution ID"):
        EvalRecord("task-1", "customer-1", "execution-2", trace, commerce)


def test_all_order_response_fields_match_across_sources() -> None:
    order_from_trace = {
        "id": 123,
        "status": "shipped",
        "user_id": 7,
        "address": {
            "address1": "1 Main",
            "address2": None,
            "city": "Boston",
            "state": "MA",
            "country": "US",
            "zip_code": "02101",
        },
        "cancel_reason": None,
        "exchange_payment_method_id": 19,
        "exchange_price_difference": 2,
        "return_payment_method_id": 20,
        "return_estimated_refund": "5",
        "items": [
            {
                "position": 0,
                "name": "Shirt",
                "product_id": 3,
                "item_id": 4,
                "price": 12,
                "options": {"size": "M"},
            }
        ],
        "payment_history": [
            {
                "position": 0,
                "transaction_type": "payment",
                "amount": 12,
                "payment_method_id": 19,
            }
        ],
        "fulfillments": [{"tracking_ids": ["T1"], "item_ids": [4]}],
        "exchange_items": [{"position": 0, "old_item_id": 4, "new_item_id": 5}],
        "return_items": [{"position": 0, "item_id": 4}],
    }
    order_from_commerce = {
        **order_from_trace,
        "order_id": "123",
        "order_status": "shipped",
        "user_id": "7",
        "address": {**order_from_trace["address"], "zip": "02101"},
        "exchange_payment_method_id": "19",
        "return_payment_method_id": "20",
        "items": [
            {
                **order_from_trace["items"][0],
                "product_id": "3",
                "item_id": "4",
                "price": "12.00",
            }
        ],
        "payment_history": [
            {
                **order_from_trace["payment_history"][0],
                "payment_method_id": "19",
                "amount": "12.00",
            }
        ],
        "fulfillments": [{"tracking_id": ["T1"], "item_ids": ["4"]}],
        "exchange_items": [{"position": 0, "old_item_id": "4", "new_item_id": "5"}],
        "return_items": [{"position": 0, "item_id": "4"}],
    }
    del order_from_commerce["id"], order_from_commerce["status"]
    del order_from_commerce["address"]["zip_code"]
    trace = AgentTrace(
        "ex",
        "tr",
        "root",
        (
            trace_run("root", "agent"),
            trace_run(
                "tool",
                "get_order",
                run_type="tool",
                inputs={"input": '{"order_id": 123, "user_id": 7}'},
                outputs={"output": {"ok": True, "data": order_from_trace}},
            ),
        ),
    )
    snapshot = CommerceSnapshot(
        "task",
        "7",
        NOW,
        {
            "user_id": 7,
            "first_name": "A",
            "last_name": "B",
            "email": "a@b",
            "address": order_from_commerce["address"],
        },
        [
            {
                "id": 19,
                "source": "card",
                "brand": "Visa",
                "last_four": "1234",
                "balance": 10,
            }
        ],
        [{"order_id": "123", "user_id": "7", "status": "shipped"}],
        {"123": order_from_commerce},
        {},
        {},
        {"lookup": 7},
    )
    actual = TraceNormalizer().normalize([trace])
    expected = CommerceNormalizer().normalize(snapshot)
    assert actual.tool_calls[0].business_result == expected.orders["123"]
    assert actual.tool_calls[0].arguments == {"order_id": "123", "customer_id": "7"}
    assert expected.customer["address"]["zip"] == "02101"
    assert expected.payment_methods["19"]["balance"] == "10.00"
    assert actual.runs[1].outputs == order_from_trace
    assert actual.runs[1].raw_outputs == {
        "output": {"ok": True, "data": order_from_trace}
    }
    assert expected.identity_lookups == {"lookup": "7"}
    assert actual.root_run_ids == ("root",)


@pytest.mark.parametrize(
    ("tool_name", "response", "expected"),
    [
        (
            "list_orders",
            [{"id": 1, "user_id": 7, "status": "shipped"}],
            [{"order_id": "1", "customer_id": "7", "order_status": "shipped"}],
        ),
        (
            "list_products",
            [{"id": 1, "name": "Shirt"}],
            [{"product_id": "1", "name": "Shirt"}],
        ),
        (
            "get_product",
            {
                "id": 1,
                "name": "Shirt",
                "variants": [
                    {
                        "id": 2,
                        "product_id": 1,
                        "product_name": "Shirt",
                        "options": {},
                        "price": 3,
                        "available": True,
                    }
                ],
            },
            {
                "product_id": "1",
                "name": "Shirt",
                "variants": [
                    {
                        "item_id": "2",
                        "product_id": "1",
                        "name": "Shirt",
                        "options": {},
                        "price": "3.00",
                        "available": True,
                    }
                ],
            },
        ),
        (
            "get_item",
            {
                "id": 2,
                "product_id": 1,
                "product_name": "Shirt",
                "options": {},
                "price": 3,
                "available": True,
            },
            {
                "item_id": "2",
                "product_id": "1",
                "name": "Shirt",
                "options": {},
                "price": "3.00",
                "available": True,
            },
        ),
        (
            "list_after_sales_cases",
            [
                {
                    "id": 8,
                    "order_id": 1,
                    "user_id": 7,
                    "type": "refund",
                    "status": "CLOSED",
                    "resolution": "refunded",
                    "reason": "damaged",
                    "created_at": "now",
                    "closed_at": "later",
                }
            ],
            [
                {
                    "case_id": "8",
                    "order_id": "1",
                    "customer_id": "7",
                    "case_type": "refund",
                    "case_status": "CLOSED",
                    "resolution": "refunded",
                    "reason": "damaged",
                    "created_at": "now",
                    "closed_at": "later",
                }
            ],
        ),
        (
            "mark_after_sales_refunded",
            {
                "case_id": 8,
                "order_id": 1,
                "customer_id": 7,
                "case_type": "refund",
                "status": "CLOSED",
                "resolution": "refunded",
                "reason": "damaged",
                "created_at": "now",
                "closed_at": "later",
            },
            {
                "case_id": "8",
                "order_id": "1",
                "customer_id": "7",
                "case_type": "refund",
                "case_status": "CLOSED",
                "resolution": "refunded",
                "reason": "damaged",
                "created_at": "now",
                "closed_at": "later",
            },
        ),
    ],
)
def test_tool_business_results_use_shared_canonical_fields(
    tool_name: str, response: object, expected: object
) -> None:
    trace = AgentTrace(
        "ex",
        "tr",
        "root",
        (
            trace_run("root", "agent"),
            trace_run(
                "tool",
                tool_name,
                run_type="tool",
                outputs={"output": {"ok": True, "data": response}},
            ),
        ),
    )
    assert (
        TraceNormalizer().normalize([trace]).tool_calls[0].business_result == expected
    )


def test_unknown_fields_are_visible_and_all_run_metadata_is_retained() -> None:
    run = TraceRun(
        id="tool",
        parent_run_id="root",
        name="get_after_sales_case",
        run_type="tool",
        inputs={"case_id": 8},
        outputs={
            "output": {
                "ok": True,
                "data": {"case_id": 8, "status": "PROCESSING", "future_field": 9},
            }
        },
        error=None,
        start_time=NOW,
        end_time=NOW,
        metadata={"tool_call_id": "call-1"},
        total_tokens=6,
        total_cost=0.02,
    )
    actual = TraceNormalizer().normalize([AgentTrace("ex", "tr", "root", (run,))])
    assert actual.tool_calls[0].business_result == {
        "case_id": "8",
        "case_status": "PROCESSING",
        "_unmapped": {"future_field": 9},
    }
    assert actual.runs[0].parent_run_id == "root"
    assert actual.runs[0].metadata == {"tool_call_id": "call-1"}
    assert actual.runs[0].total_tokens == 6
    assert actual.runs[0].total_cost == 0.02

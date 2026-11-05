"""Task success depends on database state and only required answer facts."""

from datetime import UTC, datetime

from evals.evaluation.task_success import TaskSuccessEvaluator
from evals.normalizer.models import EvalRecord, NormalizedCommerce, NormalizedTrace

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def record(
    *,
    before_order: dict,
    after_order: dict,
    answer: str | None = None,
    before_customer: dict | None = None,
    after_customer: dict | None = None,
    variants: dict | None = None,
) -> EvalRecord:
    before = NormalizedCommerce(
        "1",
        "u1",
        NOW,
        before_customer or {"customer_id": "u1"},
        {},
        {},
        {"o1": before_order},
        {},
        variants or {},
        {},
    )
    after = NormalizedCommerce(
        "1",
        "u1",
        NOW,
        after_customer or {"customer_id": "u1"},
        {},
        {},
        {"o1": after_order},
        {},
        variants or {},
        {},
    )
    trace = NormalizedTrace("execution", (), answer, (), (), ())
    return EvalRecord("1", "u1", "execution", trace, after, before)


def task(
    actions: list[dict],
    *,
    info: list[str] | None = None,
    assertions: list[str] | None = None,
) -> dict:
    return {
        "id": "1",
        "evaluation_criteria": {
            "actions": actions,
            "communicate_info": info or [],
            "nl_assertions": assertions or [],
        },
    }


def action(name: str, arguments: dict) -> dict:
    return {"action_id": "1_0", "name": name, "arguments": arguments}


def test_db_only_when_assertion_describes_action() -> None:
    before = {"order_id": "o1", "order_status": "pending", "cancel_reason": None}
    after = {**before, "order_status": "cancelled", "cancel_reason": "no longer needed"}
    result = TaskSuccessEvaluator().evaluate(
        record(before_order=before, after_order=after),
        task(
            [
                action(
                    "cancel_pending_order",
                    {"order_id": "o1", "reason": "no longer needed"},
                )
            ],
            assertions=["Agent should cancel all pending orders"],
        ),
    )
    assert result.score == 1.0
    assert result.db_success
    assert not result.nl_required
    assert result.nl_success is None


def test_required_answer_facts_use_rules_and_gate_success() -> None:
    order = {"order_id": "o1", "order_status": "delivered"}
    expected = task([], info=["8276.23", "Mastercard", "64GB"])
    passed = TaskSuccessEvaluator().evaluate(
        record(
            before_order=order,
            after_order=order,
            answer="The refund is $8,276.23 to your Mastercard. The tablet has 64 GB storage.",
        ),
        expected,
    )
    assert passed.score == 1.0
    assert passed.nl_required and passed.nl_success
    failed = TaskSuccessEvaluator().evaluate(
        record(
            before_order=order, after_order=order, answer="Your refund is $8,276.23."
        ),
        expected,
    )
    assert failed.db_success and failed.nl_success is False and failed.score == 0.0


def test_expected_item_change_and_unrequested_field_are_checked() -> None:
    before = {
        "order_id": "o1",
        "order_status": "pending",
        "items": [{"item_id": "a"}, {"item_id": "a"}, {"item_id": "c"}],
        "cancel_reason": None,
    }
    after = {
        **before,
        "order_status": "pending (item modified)",
        "items": [{"item_id": "a"}, {"item_id": "b"}, {"item_id": "c"}],
    }
    expected = task(
        [
            action(
                "modify_pending_order_items",
                {
                    "order_id": "o1",
                    "item_ids": ["a"],
                    "new_item_ids": ["b"],
                    "payment_method_id": "card1",
                },
            )
        ]
    )
    assert (
        TaskSuccessEvaluator()
        .evaluate(record(before_order=before, after_order=after), expected)
        .score
        == 1.0
    )
    wrong = {**after, "cancel_reason": "unexpected"}
    result = TaskSuccessEvaluator().evaluate(
        record(before_order=before, after_order=wrong), expected
    )
    assert not result.db_success
    assert any(
        check.name == "o1.unchanged:cancel_reason" and not check.passed
        for check in result.db_checks
    )


def test_no_expected_writes_requires_unchanged_database() -> None:
    before = {"order_id": "o1", "order_status": "pending"}
    after = {**before, "order_status": "cancelled"}
    result = TaskSuccessEvaluator().evaluate(
        record(before_order=before, after_order=after), task([])
    )
    assert result.score == 0.0


def test_exchange_checks_pairs_and_price_difference() -> None:
    before = {
        "order_id": "o1",
        "order_status": "delivered",
        "items": [{"item_id": "a", "price": "10.00"}],
        "exchange_items": [],
        "exchange_payment_method_id": None,
        "exchange_price_difference": None,
        "payment_history": [],
    }
    after = {
        **before,
        "order_status": "exchange requested",
        "exchange_items": [{"old_item_id": "a", "new_item_id": "b"}],
        "exchange_payment_method_id": "card1",
        "exchange_price_difference": "5.00",
    }
    expected = task(
        [
            action(
                "exchange_delivered_order_items",
                {
                    "order_id": "o1",
                    "item_ids": ["a"],
                    "new_item_ids": ["b"],
                    "payment_method_id": "card1",
                },
            )
        ]
    )
    observed = record(
        before_order=before, after_order=after, variants={"b": {"price": "15.00"}}
    )
    assert TaskSuccessEvaluator().evaluate(observed, expected).score == 1.0
    wrong = record(
        before_order=before,
        after_order={**after, "exchange_price_difference": "4.00"},
        variants={"b": {"price": "15.00"}},
    )
    assert TaskSuccessEvaluator().evaluate(wrong, expected).score == 0.0

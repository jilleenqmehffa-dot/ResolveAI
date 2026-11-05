"""Score Bench task outcomes from Commerce state and required answer facts."""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from evals.normalizer.models import EvalRecord

WRITE_ACTIONS = frozenset(
    {
        "modify_pending_order_items",
        "exchange_delivered_order_items",
        "return_delivered_order_items",
        "cancel_pending_order",
        "modify_pending_order_address",
        "modify_pending_order_payment",
        "modify_user_address",
    }
)
ADDRESS_FIELDS = ("address1", "address2", "city", "state", "country", "zip")
NUMBER = re.compile(r"(?<![\w#])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\w)")


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class TaskSuccessResult:
    task_id: str
    score: float
    db_success: bool
    nl_required: bool
    nl_success: bool | None
    db_checks: tuple[Check, ...]
    nl_checks: tuple[Check, ...]


def _items(order: dict[str, Any]) -> Counter[str]:
    return Counter(str(item["item_id"]) for item in order.get("items", []))


def _new_items(
    before: Counter[str], old: list[str], new: list[str]
) -> Counter[str] | None:
    if len(old) != len(new) or not old:
        return None
    result = before.copy()
    for item_id in old:
        if result[item_id] < 1:
            return None
        result[item_id] -= 1
    result.update(new)
    return +result


def _selected_prices(
    order: dict[str, Any], item_ids: list[str]
) -> list[Decimal] | None:
    remaining = list(order.get("items", []))
    prices: list[Decimal] = []
    for item_id in item_ids:
        index = next(
            (
                index
                for index, item in enumerate(remaining)
                if item.get("item_id") == item_id
            ),
            None,
        )
        if index is None:
            return None
        item = remaining.pop(index)
        if item.get("price") is None:
            return None
        prices.append(Decimal(str(item["price"])))
    return prices


def _number_values(answer: str) -> set[Decimal]:
    values: set[Decimal] = set()
    for match in NUMBER.finditer(answer):
        try:
            values.add(Decimal(match.group().replace(",", "")))
        except InvalidOperation:
            pass
    return values


def _contains_fact(answer: str, fact: str, numbers: set[Decimal]) -> bool:
    fact = str(fact).strip()
    try:
        return Decimal(fact.replace(",", "").removeprefix("$")) in numbers
    except InvalidOperation:
        words = re.findall(r"[a-z]+|\d+", fact.casefold())
        answer_words = re.findall(r"[a-z]+|\d+", answer.casefold())
        return bool(words) and any(
            answer_words[index : index + len(words)] == words
            for index in range(len(answer_words) - len(words) + 1)
        )


class TaskSuccessEvaluator:
    """Require expected Commerce changes and only explicitly required answer facts."""

    def evaluate(self, record: EvalRecord, task: dict[str, Any]) -> TaskSuccessResult:
        if str(task["id"]) != record.task_id:
            raise ValueError("Task ID does not match EvalRecord")
        if record.commerce_before is None:
            raise ValueError(
                "Task success requires a Commerce snapshot before execution"
            )
        before = record.commerce_before
        after = record.commerce
        actions = task["evaluation_criteria"]["actions"]
        checks: list[Check] = []
        expected_orders: dict[str, dict[str, Any]] = {}
        touched: dict[str, set[str]] = {}
        allowed_methods: set[str] = set()
        expected_address: dict[str, Any] | None = None

        for action in actions:
            name = action["name"]
            if name not in WRITE_ACTIONS:
                continue
            args = action["arguments"]
            action_id = action["action_id"]
            if name == "modify_user_address":
                if args["user_id"] != record.customer_id:
                    checks.append(
                        Check(action_id, False, "Expected user is not this customer")
                    )
                expected_address = {field: args[field] for field in ADDRESS_FIELDS}
                continue
            order_id = args["order_id"]
            original = before.orders.get(order_id)
            if original is None:
                checks.append(
                    Check(action_id, False, f"Baseline order {order_id} is missing")
                )
                continue
            order = expected_orders.setdefault(order_id, deepcopy(original))
            fields = touched.setdefault(order_id, set())
            fields.add("order_status")
            if name == "modify_pending_order_items":
                changed = _new_items(
                    _items(order), args["item_ids"], args["new_item_ids"]
                )
                if changed is None:
                    checks.append(
                        Check(action_id, False, "Expected item replacement is invalid")
                    )
                    continue
                order["_expected_item_counts"] = changed
                order["items"] = [
                    {"item_id": item_id}
                    for item_id, count in changed.items()
                    for _ in range(count)
                ]
                order["order_status"] = "pending (item modified)"
                fields.update({"items", "payment_history"})
                allowed_methods.add(args["payment_method_id"])
            elif name == "exchange_delivered_order_items":
                old_prices = _selected_prices(order, args["item_ids"])
                variants = [
                    before.items.get(item_id) for item_id in args["new_item_ids"]
                ]
                if old_prices is None or any(
                    variant is None or variant.get("price") is None
                    for variant in variants
                ):
                    checks.append(
                        Check(
                            action_id,
                            False,
                            "Exchange prices are missing from the baseline",
                        )
                    )
                else:
                    order["exchange_price_difference"] = (
                        f"{sum((Decimal(str(variant['price'])) - old_price for variant, old_price in zip(variants, old_prices, strict=True)), Decimal(0)):.2f}"
                    )
                pairs = Counter(
                    zip(args["item_ids"], args["new_item_ids"], strict=False)
                )
                order["_expected_exchange_pairs"] = (
                    order.get("_expected_exchange_pairs", Counter()) + pairs
                )
                order["order_status"] = "exchange requested"
                order["exchange_payment_method_id"] = args["payment_method_id"]
                fields.update(
                    {
                        "exchange_items",
                        "exchange_payment_method_id",
                        "exchange_price_difference",
                        "payment_history",
                    }
                )
                allowed_methods.add(args["payment_method_id"])
            elif name == "return_delivered_order_items":
                old_prices = _selected_prices(order, args["item_ids"])
                if old_prices is None:
                    checks.append(
                        Check(
                            action_id,
                            False,
                            "Return item prices are missing from the baseline",
                        )
                    )
                else:
                    order["return_estimated_refund"] = (
                        f"{sum(old_prices, Decimal(0)):.2f}"
                    )
                order["_expected_return_items"] = order.get(
                    "_expected_return_items", Counter()
                ) + Counter(args["item_ids"])
                order["order_status"] = "return requested"
                order["return_payment_method_id"] = args["payment_method_id"]
                fields.update(
                    {
                        "return_items",
                        "return_payment_method_id",
                        "return_estimated_refund",
                    }
                )
            elif name == "cancel_pending_order":
                order["order_status"] = "cancelled"
                order["cancel_reason"] = args["reason"].strip()
                fields.add("cancel_reason")
            elif name == "modify_pending_order_address":
                order["order_status"] = "pending (address modified)"
                order["address"] = {field: args[field] for field in ADDRESS_FIELDS}
                fields.add("address")
            elif name == "modify_pending_order_payment":
                order["order_status"] = "pending (payment modified)"
                order["_expected_payment_method"] = args["payment_method_id"]
                fields.add("payment_history")

        for order_id, expected in expected_orders.items():
            actual = after.orders.get(order_id)
            if actual is None:
                checks.append(
                    Check(order_id, False, "Order is missing after execution")
                )
                continue
            for field in touched[order_id]:
                if (
                    field == "payment_history"
                    and "_expected_payment_method" not in expected
                ):
                    continue  # Item price settlement is outside the core mutation check.
                if field == "items":
                    passed = _items(actual) == expected["_expected_item_counts"]
                elif field == "exchange_items":
                    actual_pairs = Counter(
                        (item["old_item_id"], item["new_item_id"])
                        for item in actual.get("exchange_items", [])
                    )
                    baseline_pairs = Counter(
                        (item["old_item_id"], item["new_item_id"])
                        for item in before.orders[order_id].get("exchange_items", [])
                    )
                    passed = (
                        actual_pairs - baseline_pairs
                        == expected["_expected_exchange_pairs"]
                    )
                elif field == "return_items":
                    actual_ids = Counter(
                        item["item_id"] for item in actual.get("return_items", [])
                    )
                    baseline_ids = Counter(
                        item["item_id"]
                        for item in before.orders[order_id].get("return_items", [])
                    )
                    passed = (
                        actual_ids - baseline_ids == expected["_expected_return_items"]
                    )
                elif (
                    field == "payment_history"
                    and "_expected_payment_method" in expected
                ):
                    payments = [
                        p
                        for p in actual.get("payment_history", [])
                        if p.get("transaction_type") == "payment"
                    ]
                    passed = bool(payments) and all(
                        p["payment_method_id"] == expected["_expected_payment_method"]
                        for p in payments
                    )
                else:
                    passed = actual.get(field) == expected.get(field)
                checks.append(
                    Check(f"{order_id}.{field}", passed, "Expected Commerce state")
                )
            original = before.orders[order_id]
            for field in original.keys() | actual.keys():
                if field not in touched[order_id]:
                    checks.append(
                        Check(
                            f"{order_id}.unchanged:{field}",
                            original.get(field) == actual.get(field),
                            "Unrequested order field must not change",
                        )
                    )

        for order_id in before.orders.keys() | after.orders.keys():
            if order_id not in touched:
                checks.append(
                    Check(
                        f"unrelated_order:{order_id}",
                        before.orders.get(order_id) == after.orders.get(order_id),
                        "Unrelated order must not change",
                    )
                )
        if expected_address is None:
            checks.append(
                Check(
                    "customer",
                    before.customer == after.customer,
                    "Customer profile must not change",
                )
            )
        else:
            checks.append(
                Check(
                    "customer.address",
                    after.customer.get("address") == expected_address,
                    "Expected customer address",
                )
            )
            for field in before.customer.keys() | after.customer.keys():
                if field != "address":
                    checks.append(
                        Check(
                            f"customer.unchanged:{field}",
                            before.customer.get(field) == after.customer.get(field),
                            "Unrequested customer field must not change",
                        )
                    )
        for method_id in before.payment_methods.keys() | after.payment_methods.keys():
            if method_id not in allowed_methods:
                checks.append(
                    Check(
                        f"payment_method:{method_id}",
                        before.payment_methods.get(method_id)
                        == after.payment_methods.get(method_id),
                        "Unrelated payment method must not change",
                    )
                )
        checks.append(
            Check(
                "catalog.products",
                before.products == after.products,
                "Product catalog must not change",
            )
        )
        checks.append(
            Check(
                "catalog.items",
                before.items == after.items,
                "Product variants must not change",
            )
        )

        facts = task["evaluation_criteria"].get("communicate_info") or []
        answer = record.trace.final_answer or ""
        numbers = _number_values(answer)
        nl_checks = tuple(
            Check(
                str(fact),
                _contains_fact(answer, str(fact), numbers),
                "Required answer fact",
            )
            for fact in facts
        )
        db_success = all(check.passed for check in checks)
        nl_required = bool(facts)
        nl_success = all(check.passed for check in nl_checks) if nl_required else None
        return TaskSuccessResult(
            task_id=record.task_id,
            score=float(db_success and (nl_success if nl_required else True)),
            db_success=db_success,
            nl_required=nl_required,
            nl_success=nl_success,
            db_checks=tuple(checks),
            nl_checks=nl_checks,
        )

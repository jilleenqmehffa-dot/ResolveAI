"""Canonical business fields shared by Trace and Commerce normalization."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

ID_FIELDS = frozenset(
    {
        "order_id",
        "customer_id",
        "product_id",
        "item_id",
        "payment_method_id",
        "case_id",
        "old_item_id",
        "new_item_id",
        "exchange_payment_method_id",
        "return_payment_method_id",
    }
)


def decode_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def money(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid money value: {value!r}") from exc


def _canonical(
    source: Mapping[str, Any],
    aliases: Mapping[str, tuple[str, ...]],
    *,
    amounts: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Map known names and expose unrecognized source fields explicitly."""
    result: dict[str, Any] = {}
    consumed: set[str] = set()
    for name, keys in aliases.items():
        for key in keys:
            if key in source:
                value = source[key]
                if name in ID_FIELDS and value is not None:
                    value = str(value)
                elif name in amounts:
                    value = money(value)
                result[name] = value
                consumed.update(k for k in keys if k in source)
                break
    extra = {key: value for key, value in source.items() if key not in consumed}
    if extra:
        result["_unmapped"] = extra
    return result


def _nested(result: dict[str, Any], key: str, normalizer: Any) -> None:
    if key not in result:
        return
    values = result[key]
    if values is None:
        return
    if isinstance(values, Mapping):
        values = values.values()
    result[key] = [normalizer(value) for value in values]


def normalize_address(value: Mapping[str, Any]) -> dict[str, Any]:
    return _canonical(
        value,
        {
            "address1": ("address1", "address_1"),
            "address2": ("address2", "address_2"),
            "city": ("city",),
            "state": ("state",),
            "country": ("country",),
            "zip": ("zip", "zip_code"),
        },
    )


def normalize_item(value: Mapping[str, Any]) -> dict[str, Any]:
    return _canonical(
        value,
        {
            "item_id": ("item_id", "id"),
            "product_id": ("product_id",),
            "name": ("name", "product_name"),
            "options": ("options",),
            "available": ("available",),
            "price": ("price",),
            "position": ("position",),
        },
        amounts=frozenset({"price"}),
    )


def normalize_product(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _canonical(
        value,
        {
            "product_id": ("product_id", "id"),
            "name": ("name", "product_name"),
            "variants": ("variants",),
        },
    )
    _nested(result, "variants", normalize_item)
    return result


def normalize_payment_method(value: Mapping[str, Any]) -> dict[str, Any]:
    return _canonical(
        value,
        {
            "payment_method_id": ("payment_method_id", "id"),
            "source": ("source",),
            "brand": ("brand",),
            "last_four": ("last_four",),
            "balance": ("balance",),
        },
        amounts=frozenset({"balance"}),
    )


def normalize_customer(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _canonical(
        value,
        {
            "customer_id": ("customer_id", "user_id", "id"),
            "first_name": ("first_name",),
            "last_name": ("last_name",),
            "email": ("email",),
            "address": ("address",),
            "orders": ("orders",),
            "payment_methods": ("payment_methods",),
        },
    )
    name = value.get("name")
    if isinstance(name, Mapping):
        result.setdefault("first_name", name.get("first_name"))
        result.setdefault("last_name", name.get("last_name"))
        result.get("_unmapped", {}).pop("name", None)
        if not result.get("_unmapped"):
            result.pop("_unmapped", None)
    if isinstance(result.get("address"), Mapping):
        result["address"] = normalize_address(result["address"])
    if "orders" in result:
        result["orders"] = [str(order_id) for order_id in result["orders"]]
    _nested(result, "payment_methods", normalize_payment_method)
    return result


def normalize_order_payment(value: Mapping[str, Any]) -> dict[str, Any]:
    return _canonical(
        value,
        {
            "position": ("position",),
            "transaction_type": ("transaction_type",),
            "amount": ("amount",),
            "payment_method_id": ("payment_method_id",),
        },
        amounts=frozenset({"amount"}),
    )


def normalize_fulfillment(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _canonical(
        value,
        {
            "tracking_ids": ("tracking_ids", "tracking_id"),
            "item_ids": ("item_ids",),
        },
    )
    for key in ("tracking_ids", "item_ids"):
        if key in result and result[key] is not None:
            ids = result[key]
            result[key] = (
                [str(item) for item in ids] if isinstance(ids, list) else [str(ids)]
            )
    return result


def normalize_exchange_item(value: Mapping[str, Any]) -> dict[str, Any]:
    return _canonical(
        value,
        {
            "position": ("position",),
            "old_item_id": ("old_item_id",),
            "new_item_id": ("new_item_id",),
        },
    )


def normalize_return_item(value: Mapping[str, Any]) -> dict[str, Any]:
    return _canonical(value, {"position": ("position",), "item_id": ("item_id",)})


def normalize_order(
    value: Mapping[str, Any], *, order_id: str | None = None
) -> dict[str, Any]:
    """Use one order vocabulary without inventing fields absent from a source."""
    result = _canonical(
        value,
        {
            "order_id": ("order_id", "id"),
            "order_status": ("order_status", "status"),
            "customer_id": ("customer_id", "user_id"),
            "order_position": ("order_position",),
            "address": ("address",),
            "cancel_reason": ("cancel_reason",),
            "exchange_payment_method_id": ("exchange_payment_method_id",),
            "exchange_price_difference": ("exchange_price_difference",),
            "return_payment_method_id": ("return_payment_method_id",),
            "return_estimated_refund": ("return_estimated_refund",),
            "items": ("items",),
            "payment_history": ("payment_history",),
            "fulfillments": ("fulfillments",),
            "exchange_items": ("exchange_items",),
            "return_items": ("return_items",),
        },
        amounts=frozenset({"exchange_price_difference", "return_estimated_refund"}),
    )
    if order_id is not None:
        result.setdefault("order_id", str(order_id))
    if isinstance(result.get("address"), Mapping):
        result["address"] = normalize_address(result["address"])
    for key, normalizer in (
        ("items", normalize_item),
        ("payment_history", normalize_order_payment),
        ("fulfillments", normalize_fulfillment),
        ("exchange_items", normalize_exchange_item),
        ("return_items", normalize_return_item),
    ):
        _nested(result, key, normalizer)
    return result


def normalize_after_sales_case(value: Mapping[str, Any]) -> dict[str, Any]:
    return _canonical(
        value,
        {
            "case_id": ("case_id", "id"),
            "order_id": ("order_id",),
            "customer_id": ("customer_id", "user_id"),
            "case_type": ("case_type", "type"),
            "case_status": ("case_status", "status"),
            "resolution": ("resolution",),
            "reason": ("reason",),
            "created_at": ("created_at",),
            "closed_at": ("closed_at",),
        },
    )

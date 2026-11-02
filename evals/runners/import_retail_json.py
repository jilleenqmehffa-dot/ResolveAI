"""Import the nested retail benchmark JSON into the current database schema."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Numeric, select
from sqlalchemy.engine import Engine

from services.commerce_service.app.db.session import get_engine
from services.commerce_service.app.models import Base

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "datasets/db.json"
ADDRESS_FIELDS = ("address1", "address2", "city", "state", "country", "zip")


def build_rows(source: Path) -> dict[str, list[dict[str, Any]]]:
    """Flatten the source JSON while retaining nested list positions."""
    database = json.loads(source.read_text(encoding="utf-8"), parse_float=Decimal)
    if set(database) != {"products", "users", "orders"}:
        raise ValueError("Expected products, users, and orders in the source JSON")
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in Base.metadata.tables}

    for product_id, product in database["products"].items():
        if product["product_id"] != product_id:
            raise ValueError(f"Product ID differs from its JSON key: {product_id}")
        rows["products"].append({"product_id": product_id, "name": product["name"]})
        for item_id, variant in product["variants"].items():
            if variant["item_id"] != item_id:
                raise ValueError(f"Variant ID differs from its JSON key: {item_id}")
            rows["product_variants"].append(
                {
                    "item_id": item_id,
                    "product_id": product_id,
                    "options_json": json.dumps(
                        variant["options"], ensure_ascii=False, sort_keys=True
                    ),
                    "available": variant["available"],
                    "price": variant["price"],
                }
            )

    for user_id, user in database["users"].items():
        if user["user_id"] != user_id:
            raise ValueError(f"User ID differs from its JSON key: {user_id}")
        rows["users"].append(
            {
                "user_id": user_id,
                "first_name": user["name"]["first_name"],
                "last_name": user["name"]["last_name"],
                "email": user["email"],
                **{field: user["address"].get(field) for field in ADDRESS_FIELDS},
            }
        )
        for method_id, method in user["payment_methods"].items():
            if method["id"] != method_id:
                raise ValueError(
                    f"Payment method ID differs from its JSON key: {method_id}"
                )
            rows["user_payment_methods"].append(
                {
                    "payment_method_id": method_id,
                    "user_id": user_id,
                    "source": method["source"],
                    "brand": method.get("brand"),
                    "last_four": method.get("last_four"),
                    "balance": method.get("balance"),
                }
            )
        for position, order_id in enumerate(user["orders"]):
            rows["user_order_links"].append(
                {"user_id": user_id, "order_id": order_id, "position": position}
            )

    for order_id, order in database["orders"].items():
        if order["order_id"] != order_id:
            raise ValueError(f"Order ID differs from its JSON key: {order_id}")
        rows["orders"].append(
            {
                "order_id": order_id,
                "user_id": order["user_id"],
                "status": order["status"],
                "cancel_reason": order.get("cancel_reason"),
                "exchange_payment_method_id": order.get("exchange_payment_method_id"),
                "exchange_price_difference": order.get("exchange_price_difference"),
                "return_payment_method_id": order.get("return_payment_method_id"),
                **{field: order["address"].get(field) for field in ADDRESS_FIELDS},
            }
        )
        for position, item in enumerate(order["items"]):
            rows["order_items"].append(
                {
                    "order_id": order_id,
                    "position": position,
                    "name": item["name"],
                    "product_id": item["product_id"],
                    "item_id": item["item_id"],
                    "price": item["price"],
                    "options_json": json.dumps(
                        item["options"], ensure_ascii=False, sort_keys=True
                    ),
                }
            )
        for fulfillment_index, fulfillment in enumerate(order["fulfillments"]):
            for position, tracking_id in enumerate(fulfillment["tracking_id"]):
                rows["order_fulfillment_tracking"].append(
                    {
                        "order_id": order_id,
                        "fulfillment_index": fulfillment_index,
                        "position": position,
                        "tracking_id": tracking_id,
                    }
                )
            for position, item_id in enumerate(fulfillment["item_ids"]):
                rows["order_fulfillment_items"].append(
                    {
                        "order_id": order_id,
                        "fulfillment_index": fulfillment_index,
                        "position": position,
                        "item_id": item_id,
                    }
                )
        for position, payment in enumerate(order["payment_history"]):
            rows["order_payments"].append(
                {"order_id": order_id, "position": position, **payment}
            )
        old_items = order.get("exchange_items") or []
        new_items = order.get("exchange_new_items") or []
        if len(old_items) != len(new_items):
            raise ValueError(
                f"Exchange item lists differ in length for order {order_id}"
            )
        for position, (old_item_id, new_item_id) in enumerate(
            zip(old_items, new_items, strict=True)
        ):
            rows["order_exchange_items"].append(
                {
                    "order_id": order_id,
                    "position": position,
                    "old_item_id": old_item_id,
                    "new_item_id": new_item_id,
                }
            )
        for position, item_id in enumerate(order.get("return_items") or []):
            rows["order_return_items"].append(
                {"order_id": order_id, "position": position, "item_id": item_id}
            )
    return rows


def _normalized(value: Any, column_type: Any) -> Any:
    if value is not None and isinstance(column_type, Numeric):
        return Decimal(str(value))
    if value is not None and isinstance(column_type, Boolean):
        return bool(value)
    return value


def _matches_existing(connection: Any, rows: dict[str, list[dict[str, Any]]]) -> bool:
    for table in Base.metadata.sorted_tables:
        expected = rows[table.name]
        actual = connection.execute(select(table)).mappings().all()
        if len(actual) != len(expected):
            return False
        keys = [column.name for column in table.primary_key.columns]
        existing = {tuple(row[key] for key in keys): dict(row) for row in actual}
        for row in expected:
            key = tuple(row[field] for field in keys)
            if key not in existing:
                return False
            if any(
                _normalized(existing[key][column.name], column.type)
                != _normalized(row.get(column.name), column.type)
                for column in table.columns
            ):
                return False
    return True


def import_json(
    engine: Engine, source: Path = DEFAULT_SOURCE
) -> tuple[bool, dict[str, int]]:
    """Import atomically into empty tables; skip an identical prior import."""
    rows = build_rows(source)
    counts = {name: len(items) for name, items in rows.items()}
    with engine.begin() as connection:
        populated = any(
            connection.execute(select(table).limit(1)).first() is not None
            for table in Base.metadata.sorted_tables
        )
        if populated:
            if _matches_existing(connection, rows):
                return False, counts
            raise RuntimeError(
                "Retail tables contain other data; refusing to overwrite or mix datasets"
            )
        for table in Base.metadata.sorted_tables:
            if rows[table.name]:
                connection.execute(table.insert(), rows[table.name])
    return True, counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    imported, counts = import_json(get_engine(), args.source)
    print("Imported" if imported else "Already imported")
    for name, count in counts.items():
        print(f"{name}: {count}")


if __name__ == "__main__":
    main()

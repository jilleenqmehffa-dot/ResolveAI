"""Align retail storage with the benchmark CSV tables.

Revision ID: 8c2f0a4d91b7
Revises: 6f2c9d1a4b70
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from services.commerce_service.app.models import Base

revision: str = "8c2f0a4d91b7"
down_revision: str | Sequence[str] | None = "6f2c9d1a4b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TABLES = (
    "fulfillment_items",
    "order_return_items",
    "order_exchange_items",
    "order_payments",
    "order_items",
    "fulfillments",
    "orders",
    "variants",
    "payment_methods",
    "users",
    "products",
)
RETAIL_TABLES = frozenset(
    {
        "products",
        "product_variants",
        "users",
        "user_payment_methods",
        "user_order_links",
        "orders",
        "order_items",
        "order_fulfillment_tracking",
        "order_fulfillment_items",
        "order_payments",
        "order_exchange_items",
        "order_return_items",
    }
)
ADDRESS_FIELDS = ("address1", "address2", "city", "state", "country", "zip")


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _address(value):
    address = _json(value) or {}
    return {field: address.get(field) or None for field in ADDRESS_FIELDS}


def upgrade() -> None:
    bind = op.get_bind()
    old = {
        name: [
            dict(row) for row in bind.execute(text(f"SELECT * FROM {name}")).mappings()
        ]
        for name in OLD_TABLES
    }

    new: dict[str, list[dict]] = defaultdict(list)
    new["products"] = old["products"]
    for row in old["variants"]:
        new["product_variants"].append(
            {
                "item_id": row["item_id"],
                "product_id": row["product_id"],
                "options_json": json.dumps(
                    _json(row["options"]), ensure_ascii=False, sort_keys=True
                ),
                "available": row["available"],
                "price": row["price"],
            }
        )
    for row in old["users"]:
        new["users"].append(
            {
                "user_id": row["user_id"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "email": row["email"],
                **_address(row["address"]),
            }
        )
    for row in old["payment_methods"]:
        new["user_payment_methods"].append(
            {
                "payment_method_id": row["id"],
                "user_id": row["user_id"],
                "source": row["source"],
                "brand": row["brand"],
                "last_four": row["last_four"],
                "balance": row["balance"],
            }
        )
    for row in old["orders"]:
        new["orders"].append(
            {
                "order_id": row["order_id"],
                "user_id": row["user_id"],
                "status": row["status"],
                "cancel_reason": row["cancel_reason"],
                "exchange_payment_method_id": row["exchange_payment_method_id"],
                "exchange_price_difference": row["exchange_price_difference"],
                "return_payment_method_id": row["return_payment_method_id"],
                **_address(row["address"]),
            }
        )
    links: dict[str, list[str]] = defaultdict(list)
    for row in sorted(old["orders"], key=lambda row: row["order_id"]):
        links[row["user_id"]].append(row["order_id"])
    for user_id, order_ids in links.items():
        for position, order_id in enumerate(order_ids):
            new["user_order_links"].append(
                {
                    "user_id": user_id,
                    "order_id": order_id,
                    "position": position,
                }
            )

    for source, target, extra in (
        (
            "order_items",
            "order_items",
            lambda row: {
                "name": row["name"],
                "product_id": row["product_id"],
                "item_id": row["item_id"],
                "price": row["price"],
                "options_json": json.dumps(
                    _json(row["options"]), ensure_ascii=False, sort_keys=True
                ),
            },
        ),
        (
            "order_payments",
            "order_payments",
            lambda row: {
                "transaction_type": row["transaction_type"],
                "amount": row["amount"],
                "payment_method_id": row["payment_method_id"],
            },
        ),
        (
            "order_exchange_items",
            "order_exchange_items",
            lambda row: {
                "old_item_id": row["original_item_id"],
                "new_item_id": row["new_item_id"],
            },
        ),
        (
            "order_return_items",
            "order_return_items",
            lambda row: {"item_id": row["item_id"]},
        ),
    ):
        positions: dict[str, int] = defaultdict(int)
        for row in sorted(old[source], key=lambda row: row["id"]):
            order_id = row["order_id"]
            new[target].append(
                {
                    "order_id": order_id,
                    "position": positions[order_id],
                    **extra(row),
                }
            )
            positions[order_id] += 1

    fulfillment_indexes: dict[str, int] = defaultdict(int)
    by_legacy_id: dict[int, tuple[str, int]] = {}
    for row in sorted(old["fulfillments"], key=lambda row: row["id"]):
        order_id = row["order_id"]
        index = fulfillment_indexes[order_id]
        fulfillment_indexes[order_id] += 1
        by_legacy_id[row["id"]] = (order_id, index)
        for position, tracking_id in enumerate(_json(row["tracking_id"])):
            new["order_fulfillment_tracking"].append(
                {
                    "order_id": order_id,
                    "fulfillment_index": index,
                    "position": position,
                    "tracking_id": tracking_id,
                }
            )
    item_positions: dict[int, int] = defaultdict(int)
    for row in sorted(old["fulfillment_items"], key=lambda row: row["id"]):
        fulfillment_id = row["fulfillment_id"]
        order_id, index = by_legacy_id[fulfillment_id]
        new["order_fulfillment_items"].append(
            {
                "order_id": order_id,
                "fulfillment_index": index,
                "position": item_positions[fulfillment_id],
                "item_id": row["item_id"],
            }
        )
        item_positions[fulfillment_id] += 1

    for name in OLD_TABLES:
        op.drop_table(name)
    # This revision predates after-sales and Agent tables. Limit it to the
    # retail schema it owns, regardless of later additions to Base.metadata.
    retail_tables = [
        table for table in Base.metadata.sorted_tables if table.name in RETAIL_TABLES
    ]
    Base.metadata.create_all(bind, tables=retail_tables)
    for table in retail_tables:
        rows = new[table.name]
        if rows:
            bind.execute(table.insert(), rows)


def downgrade() -> None:
    raise NotImplementedError(
        "The benchmark schema migration cannot be safely reversed."
    )

"""The real benchmark JSON can be represented by the retail tables."""

import json
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, func, select

from alembic import command
from benchmark.runners.import_retail_json import import_json
from services.commerce_service.app.core.config import get_settings
from services.commerce_service.app.models import Base

DATASET = Path(__file__).resolve().parents[2] / "benchmark/datasets/db.json"
ADDRESS_FIELDS = ("address1", "address2", "city", "state", "country", "zip")


def test_benchmark_json_fits_retail_schema() -> None:
    database = json.loads(DATASET.read_text(encoding="utf-8"))
    assert set(database) == {"products", "users", "orders"}
    assert database["products"] and database["users"] and database["orders"]
    rows: dict[str, list[dict]] = {name: [] for name in Base.metadata.tables}

    for product_id, product in database["products"].items():
        assert product["product_id"] == product_id
        rows["products"].append({"product_id": product_id, "name": product["name"]})
        for item_id, variant in product["variants"].items():
            assert variant["item_id"] == item_id
            rows["product_variants"].append(
                {
                    "item_id": item_id,
                    "product_id": product_id,
                    "options_json": json.dumps(variant["options"]),
                    "available": variant["available"],
                    "price": variant["price"],
                }
            )

    for user_id, user in database["users"].items():
        assert user["user_id"] == user_id
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
            assert method["id"] == method_id
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
        assert order["order_id"] == order_id
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
                    "options_json": json.dumps(item["options"]),
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
        assert len(old_items) == len(new_items)
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

    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if rows[table.name]:
                connection.execute(table.insert(), rows[table.name])
            assert connection.scalar(select(func.count()).select_from(table)) == len(
                rows[table.name]
            )
    engine.dispose()


def test_json_runner_imports_once_and_rejects_other_data(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'retail.sqlite'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    imported, counts = import_json(engine, DATASET)
    assert imported
    assert counts["products"] == 50
    assert counts["product_variants"] == 591
    assert counts["users"] == 500
    assert counts["orders"] == 1000
    assert counts["order_items"] == 2978
    assert import_json(engine, DATASET) == (False, counts)

    with engine.begin() as connection:
        product = Base.metadata.tables["products"]
        first_id = connection.execute(
            select(product.c.product_id).limit(1)
        ).scalar_one()
        connection.execute(
            product.update()
            .where(product.c.product_id == first_id)
            .values(name="Changed")
        )
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        import_json(engine, DATASET)
    engine.dispose()


def test_upgrade_preserves_legacy_retail_rows(tmp_path, monkeypatch) -> None:
    database = tmp_path / "legacy.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "6f2c9d1a4b70")
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO products VALUES ('p1', 'Shirt')")
            connection.execute(
                "INSERT INTO variants VALUES ('sku1', 'p1', ?, 1, 25)",
                ('{"size":"M"}',),
            )
            connection.execute(
                "INSERT INTO users VALUES ('u1', 'Ada', 'Lin', ?, 'ada@example.com')",
                ('{"address1":"1 Main St","city":"Shanghai","country":"CN"}',),
            )
            connection.execute(
                "INSERT INTO payment_methods (id, user_id, source) VALUES ('cc1', 'u1', 'credit_card')"
            )
            connection.execute(
                "INSERT INTO orders (order_id, user_id, address, status) VALUES ('o1', 'u1', ?, 'delivered')",
                ('{"address1":"1 Main St","city":"Shanghai","country":"CN"}',),
            )
            for _ in range(2):
                connection.execute(
                    "INSERT INTO order_items (order_id, name, product_id, item_id, price, options) "
                    "VALUES ('o1', 'Shirt', 'p1', 'sku1', 25, ?)",
                    ('{"size":"M"}',),
                )
            connection.execute(
                "INSERT INTO fulfillments (order_id, tracking_id) VALUES ('o1', ?)",
                ('["track1"]',),
            )
            connection.execute(
                "INSERT INTO fulfillment_items (fulfillment_id, item_id) VALUES (1, 'sku1')"
            )
            connection.execute(
                "INSERT INTO order_payments (order_id, transaction_type, amount, payment_method_id) "
                "VALUES ('o1', 'payment', 50, 'cc1')"
            )

        command.upgrade(config, "head")
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT position, item_id FROM order_items ORDER BY position"
            ).fetchall() == [(0, "sku1"), (1, "sku1")]
            assert connection.execute(
                "SELECT fulfillment_index, position, tracking_id "
                "FROM order_fulfillment_tracking"
            ).fetchall() == [(0, 0, "track1")]
            assert connection.execute(
                "SELECT fulfillment_index, position, item_id "
                "FROM order_fulfillment_items"
            ).fetchall() == [(0, 0, "sku1")]
            assert connection.execute(
                "SELECT user_id, order_id, position FROM user_order_links"
            ).fetchall() == [("u1", "o1", 0)]
            assert connection.execute(
                "SELECT address1, city, country FROM users"
            ).fetchall() == [("1 Main St", "Shanghai", "CN")]
    finally:
        get_settings.cache_clear()

"""Restore one evaluated customer's Commerce rows after a task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import Connection
from sqlalchemy.sql.schema import Table

from services.commerce_service.app.models import Base

TABLES = tuple(Base.metadata.sorted_tables)
ORDER_TABLES = frozenset(
    {
        "order_exchange_items",
        "order_fulfillment_items",
        "order_fulfillment_tracking",
        "order_items",
        "order_payments",
        "order_return_items",
    }
)
CUSTOMER_TABLES = frozenset(
    {"users", "orders", "user_payment_methods", "after_sales_cases", "user_order_links"}
) | ORDER_TABLES


@dataclass(frozen=True)
class CustomerCheckpoint:
    customer_id: str
    rows: dict[str, list[dict[str, Any]]]


def _customer_orders(connection: Connection, customer_id: str) -> tuple[str, ...]:
    orders = Base.metadata.tables["orders"]
    return tuple(connection.scalars(select(orders.c.order_id).where(orders.c.user_id == customer_id)))


def _condition(table: Table, customer_id: str, order_ids: tuple[str, ...]):
    if table.name == "users":
        return table.c.user_id == customer_id
    if table.name == "orders" or table.name == "user_payment_methods":
        return table.c.user_id == customer_id
    if table.name == "after_sales_cases":
        return table.c.customer_id == customer_id
    if table.name == "user_order_links":
        return table.c.user_id == customer_id
    return table.c.order_id.in_(order_ids)


def _read_rows(connection: Connection, customer_id: str) -> dict[str, list[dict[str, Any]]]:
    order_ids = _customer_orders(connection, customer_id)
    return {
        table.name: [dict(row) for row in connection.execute(
            select(table)
            .where(_condition(table, customer_id, order_ids))
            .order_by(*table.primary_key.columns)
        ).mappings()]
        for table in TABLES
        if table.name in CUSTOMER_TABLES
    }


def capture_customer(engine: Engine, customer_id: str) -> CustomerCheckpoint:
    """Save all mutable rows reachable through this customer's Commerce tools."""
    with engine.connect() as connection:
        rows = _read_rows(connection, customer_id)
    if len(rows["users"]) != 1:
        raise ValueError(f"Customer {customer_id} does not exist in Commerce database")
    return CustomerCheckpoint(customer_id, rows)


def restore_customer(engine: Engine, checkpoint: CustomerCheckpoint) -> bool:
    """Restore the saved rows atomically; return whether anything changed."""
    with engine.begin() as connection:
        current = _read_rows(connection, checkpoint.customer_id)
        if current == checkpoint.rows:
            return False
        current_order_ids = tuple(row["order_id"] for row in current["orders"])
        saved_order_ids = tuple(row["order_id"] for row in checkpoint.rows["orders"])
        order_ids = tuple(set(current_order_ids) | set(saved_order_ids))
        for table in reversed(TABLES):
            if table.name in CUSTOMER_TABLES:
                connection.execute(table.delete().where(
                    _condition(table, checkpoint.customer_id, order_ids)
                ))
        for table in TABLES:
            if table.name in CUSTOMER_TABLES and checkpoint.rows[table.name]:
                connection.execute(table.insert(), checkpoint.rows[table.name])
    return True


def with_engine(database_url: str) -> Engine:
    """Build the same SQLAlchemy engine used by the Commerce service."""
    return create_engine(database_url, pool_pre_ping=True)

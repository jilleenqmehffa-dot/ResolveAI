"""A task restores only its customer's mutable Commerce rows."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select

from evals.adapter.commerce_checkpoint import capture_customer, restore_customer
from services.commerce_service.app.models import Base


def test_customer_checkpoint_restores_writes_and_preserves_other_customer():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    tables = Base.metadata.tables
    case_id = uuid4()
    with engine.begin() as connection:
        connection.execute(tables["users"].insert(), [
            {"user_id": "u1", "first_name": "One", "last_name": "User", "email": "one@example.com"},
            {"user_id": "u2", "first_name": "Two", "last_name": "User", "email": "two@example.com"},
        ])
        connection.execute(tables["orders"].insert(), [
            {"order_id": "o1", "user_id": "u1", "status": "pending", "address2": "Suite 640"},
            {"order_id": "o2", "user_id": "u2", "status": "pending", "address2": "Suite 200"},
        ])
        connection.execute(tables["user_payment_methods"].insert(), [
            {"payment_method_id": "p1", "user_id": "u1", "source": "gift_card", "balance": 50},
            {"payment_method_id": "p2", "user_id": "u2", "source": "gift_card", "balance": 75},
        ])
        connection.execute(tables["after_sales_cases"].insert(), {
            "case_id": case_id, "order_id": "o1", "customer_id": "u1",
            "case_type": "REFUND_ONLY", "status": "PROCESSING", "reason": "refund",
            "created_at": datetime.now(UTC),
        })

    saved = capture_customer(engine, "u1")
    other = capture_customer(engine, "u2")
    with engine.begin() as connection:
        connection.execute(tables["users"].update().where(tables["users"].c.user_id == "u1").values(address2="Suite 641"))
        connection.execute(tables["orders"].update().where(tables["orders"].c.order_id == "o1").values(status="pending (address modified)", address2="Suite 641"))
        connection.execute(tables["user_payment_methods"].update().where(tables["user_payment_methods"].c.payment_method_id == "p1").values(balance=25))
        connection.execute(tables["after_sales_cases"].update().where(tables["after_sales_cases"].c.case_id == case_id).values(status="CLOSED", resolution="REFUNDED", closed_at=datetime.now(UTC)))
        connection.execute(tables["order_payments"].insert(), {
            "order_id": "o1", "position": 0, "transaction_type": "refund",
            "amount": 25, "payment_method_id": "p1",
        })

    assert restore_customer(engine, saved) is True
    assert capture_customer(engine, "u1") == saved
    assert capture_customer(engine, "u2") == other
    assert restore_customer(engine, saved) is False
    with engine.connect() as connection:
        assert connection.scalar(select(tables["order_payments"].c.order_id)) is None
    engine.dispose()

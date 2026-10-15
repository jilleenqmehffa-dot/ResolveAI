"""Retail rules against a small transactional SQLAlchemy database."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from services.commerce_service.app.core.exceptions import (
    ConflictError,
    InsufficientBalanceError,
    NotFoundError,
    ValidationError,
)
from services.commerce_service.app.models import (
    Base,
    Order,
    OrderItem,
    OrderPayment,
    PaymentMethod,
    Product,
    User,
    Variant,
)
from services.commerce_service.app.repositories.retail import ExchangeRepository
from services.commerce_service.app.services.retail_service import RetailService


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'retail.db'}", connect_args={"timeout": 5}
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def lock_writes_early(connection) -> None:
        # SQLite has no SELECT FOR UPDATE. Reserve its writer lock before reading.
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add_all(
            [
                Product(
                    product_id="p1",
                    name="Shirt",
                    variants=[
                        Variant(
                            item_id="a",
                            options_json='{"size": "S"}',
                            price=Decimal("10.00"),
                            available=True,
                        ),
                        Variant(
                            item_id="b",
                            options_json='{"size": "M"}',
                            price=Decimal("15.00"),
                            available=True,
                        ),
                        Variant(
                            item_id="c",
                            options_json='{"size": "L"}',
                            price=Decimal("7.00"),
                            available=True,
                        ),
                        Variant(
                            item_id="z",
                            options_json='{"size": "XL"}',
                            price=Decimal("20.00"),
                            available=False,
                        ),
                    ],
                ),
                Product(
                    product_id="p2",
                    name="Bag",
                    variants=[
                        Variant(
                            item_id="x",
                            options_json='{"color": "red"}',
                            price=Decimal("30.00"),
                            available=True,
                        )
                    ],
                ),
                User(
                    user_id="u1",
                    first_name="Ada",
                    last_name="Lin",
                    email="ada@example.com",
                    payment_methods=[
                        PaymentMethod(payment_method_id="card1", source="credit_card"),
                        PaymentMethod(payment_method_id="card3", source="credit_card"),
                        PaymentMethod(
                            payment_method_id="gift1", source="gift_card", balance=Decimal("6.00")
                        ),
                    ],
                ),
                User(
                    user_id="u2",
                    first_name="Bob",
                    last_name="Chen",
                    email="bob@example.com",
                    payment_methods=[PaymentMethod(payment_method_id="card2", source="credit_card")],
                ),
            ]
        )
    with factory.begin() as session:
        for order_id, status in (("pending", "pending"), ("delivered", "delivered")):
            session.add(
                Order(
                    order_id=order_id,
                    user_id="u1",
                    status=status,
                    items=[
                        OrderItem(
                            position=0,
                            name="Shirt",
                            product_id="p1",
                            item_id="a",
                            price=Decimal("10.00"),
                            options_json='{"size": "S"}',
                        ),
                        OrderItem(
                            position=1,
                            name="Shirt",
                            product_id="p1",
                            item_id="a",
                            price=Decimal("10.00"),
                            options_json='{"size": "S"}',
                        ),
                        OrderItem(
                            position=2,
                            name="Shirt",
                            product_id="p1",
                            item_id="b",
                            price=Decimal("15.00"),
                            options_json='{"size": "M"}',
                        ),
                    ],
                    payment_history=[
                        OrderPayment(
                            position=0,
                            transaction_type="payment",
                            amount=Decimal("35.00"),
                            payment_method_id="card1",
                        )
                    ],
                )
            )
    yield factory
    engine.dispose()


def test_catalog_reads(sessions) -> None:
    with sessions() as session:
        service = RetailService(session)
        assert [
            (item.name, item.product_id) for item in service.list_all_product_types()
        ] == [
            ("Bag", "p2"),
            ("Shirt", "p1"),
        ]
        product = service.get_product_details("p1")
        assert {item.item_id for item in product.variants} == {"a", "b", "c", "z"}
        assert service.get_item_details("b").product_name == "Shirt"
        assert service.get_item_details("z").available is False
        with pytest.raises(NotFoundError):
            service.get_product_details("missing")
        with pytest.raises(NotFoundError):
            service.get_item_details("missing")


@pytest.mark.parametrize("operation", ["modify", "exchange", "return"])
def test_owner_is_checked(sessions, operation: str) -> None:
    with sessions() as session:
        service = RetailService(session)
        with pytest.raises(NotFoundError):
            if operation == "modify":
                service.modify_pending_order_items(
                    "pending", ["a"], ["b"], "card2", "u2"
                )
            elif operation == "exchange":
                service.exchange_delivered_order_items(
                    "delivered", ["a"], ["b"], "card2", "u2"
                )
            else:
                service.return_delivered_order_items("delivered", ["a"], "card2", "u2")


def test_status_and_count_rules(sessions) -> None:
    with sessions() as session:
        service = RetailService(session)
        with pytest.raises(ConflictError):
            service.modify_pending_order_items("delivered", ["a"], ["b"], "card1", "u1")
        with pytest.raises(ConflictError):
            service.exchange_delivered_order_items(
                "pending", ["a"], ["b"], "card1", "u1"
            )
        with pytest.raises(ConflictError):
            service.return_delivered_order_items("pending", ["a"], "card1", "u1")
        with pytest.raises(ValidationError):
            service.modify_pending_order_items("pending", ["a"], [], "card1", "u1")
        with pytest.raises(ValidationError):
            service.exchange_delivered_order_items(
                "delivered", ["a"], [], "card1", "u1"
            )
        with pytest.raises(ValidationError):
            service.return_delivered_order_items(
                "delivered", ["a", "a", "a"], "card1", "u1"
            )


@pytest.mark.parametrize("new_id", ["a", "x", "z"])
@pytest.mark.parametrize("operation", ["modify", "exchange"])
def test_invalid_new_variant(sessions, new_id: str, operation: str) -> None:
    with sessions() as session:
        service = RetailService(session)
        with pytest.raises(ValidationError):
            if operation == "modify":
                service.modify_pending_order_items(
                    "pending", ["a"], [new_id], "card1", "u1"
                )
            else:
                service.exchange_delivered_order_items(
                    "delivered", ["a"], [new_id], "card1", "u1"
                )


def test_duplicate_quantity_and_payment_ownership(sessions) -> None:
    with sessions() as session:
        service = RetailService(session)
        with pytest.raises(ValidationError):
            service.modify_pending_order_items(
                "pending", ["a", "a", "a"], ["b", "b", "b"], "card1", "u1"
            )
        with pytest.raises(ValidationError):
            service.exchange_delivered_order_items(
                "delivered", ["a", "a", "a"], ["b", "b", "b"], "card1", "u1"
            )
        with pytest.raises(NotFoundError):
            service.modify_pending_order_items("pending", ["a"], ["b"], "card2", "u1")


def test_pending_change_updates_each_item_and_charges_once(sessions) -> None:
    with sessions() as session:
        order = RetailService(session).modify_pending_order_items(
            "pending", ["a", "a"], ["b", "c"], "card1", "u1"
        )
        assert order.status == "pending (item modified)"
        assert [
            (item.item_id, item.price, item.options_json) for item in order.items[:2]
        ] == [
            ("b", Decimal("15.00"), '{"size": "M"}'),
            ("c", Decimal("7.00"), '{"size": "L"}'),
        ]
        assert [(p.transaction_type, p.amount) for p in order.payment_history][-1] == (
            "payment",
            Decimal("2.00"),
        )
        with pytest.raises(ConflictError):
            RetailService(session).modify_pending_order_items(
                "pending", ["b"], ["c"], "card1", "u1"
            )


def test_gift_card_payment_refund_and_insufficient_balance(sessions) -> None:
    with sessions() as session:
        with pytest.raises(InsufficientBalanceError):
            RetailService(session).modify_pending_order_items(
                "pending", ["a", "a"], ["b", "b"], "gift1", "u1"
            )
        assert session.get(PaymentMethod, "gift1").balance == Decimal("6.00")
    with sessions() as session:
        order = RetailService(session).modify_pending_order_items(
            "pending", ["a"], ["b"], "gift1", "u1"
        )
        assert session.get(PaymentMethod, "gift1").balance == Decimal("1.00")
        assert order.payment_history[-1].transaction_type == "payment"
    with sessions() as session:
        order = RetailService(session).exchange_delivered_order_items(
            "delivered", ["b"], ["c"], "gift1", "u1"
        )
        assert session.get(PaymentMethod, "gift1").balance == Decimal("9.00")
        assert (
            order.payment_history[-1].transaction_type,
            order.payment_history[-1].amount,
        ) == ("refund", Decimal("8.00"))
        assert [
            (item.old_item_id, item.new_item_id) for item in order.exchange_items
        ] == [("b", "c")]
        assert order.exchange_price_difference == Decimal("-8.00")
        assert [item.item_id for item in order.items] == ["a", "a", "b"]
        with pytest.raises(ConflictError):
            RetailService(session).return_delivered_order_items(
                "delivered", ["a"], "card1", "u1"
            )


def test_return_request_is_not_a_refund(sessions) -> None:
    with sessions() as session:
        service = RetailService(session)
        with pytest.raises(ValidationError):
            service.return_delivered_order_items("delivered", ["a"], "card3", "u1")
        order = service.return_delivered_order_items(
            "delivered", ["a", "b"], "gift1", "u1"
        )
        assert order.status == "return requested"
        assert order.return_estimated_refund == Decimal("25.00")
        assert [item.item_id for item in order.return_items] == ["a", "b"]
        assert len(order.payment_history) == 1
        assert session.get(PaymentMethod, "gift1").balance == Decimal("6.00")
        with pytest.raises(ConflictError):
            service.exchange_delivered_order_items(
                "delivered", ["a"], ["b"], "card1", "u1"
            )


def test_exception_rolls_back_entire_exchange(sessions, monkeypatch) -> None:
    def fail_after_payment(self, order, pairs) -> None:
        self.session.flush()
        raise RuntimeError("request storage failed")

    monkeypatch.setattr(ExchangeRepository, "create_request", fail_after_payment)
    with sessions() as session, pytest.raises(RuntimeError):
        RetailService(session).exchange_delivered_order_items(
            "delivered", ["a"], ["b"], "gift1", "u1"
        )
    with sessions() as session:
        order = session.get(Order, "delivered")
        assert order.status == "delivered"
        assert session.get(PaymentMethod, "gift1").balance == Decimal("6.00")
        assert (
            session.scalar(
                select(func.count(OrderPayment.position)).where(
                    OrderPayment.order_id == "delivered"
                )
            )
            == 1
        )


def test_existing_transaction_is_reused_without_savepoint(sessions) -> None:
    with sessions.begin() as session:
        transaction = session.get_transaction()
        order = RetailService(session).modify_pending_order_items(
            "pending", ["a"], ["b"], "card1", "u1"
        )
        assert session.get_transaction() is transaction
        assert not session.in_nested_transaction()
        assert order.status == "pending (item modified)"

    with sessions() as session:
        assert session.get(Order, "pending").status == "pending (item modified)"


def test_failure_rolls_back_existing_transaction(sessions, monkeypatch) -> None:
    def fail_after_flush(self, order, pairs) -> None:
        self.session.flush()
        raise RuntimeError("request storage failed")

    monkeypatch.setattr(ExchangeRepository, "create_request", fail_after_flush)
    with sessions.begin() as session:
        session.get(User, "u1").first_name = "Changed"
        session.flush()
        with pytest.raises(RuntimeError):
            RetailService(session).exchange_delivered_order_items(
                "delivered", ["a"], ["b"], "gift1", "u1"
            )
        assert not session.in_transaction()

    with sessions() as session:
        assert session.get(User, "u1").first_name == "Ada"
        assert session.get(Order, "delivered").status == "delivered"
        assert session.get(PaymentMethod, "gift1").balance == Decimal("6.00")
        assert (
            session.scalar(
                select(func.count(OrderPayment.position)).where(
                    OrderPayment.order_id == "delivered"
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    ("old_id", "new_id", "expected_balance", "kind"),
    [
        ("a", "b", Decimal("1.00"), "payment"),
        ("b", "c", Decimal("14.00"), "refund"),
    ],
)
def test_concurrent_exchange_can_only_settle_and_create_once(
    sessions,
    monkeypatch,
    old_id: str,
    new_id: str,
    expected_balance: Decimal,
    kind: str,
) -> None:
    entered = Event()
    release = Event()
    original = RetailService._changes

    def pause_first(self, order, item_ids, new_item_ids):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(self, order, item_ids, new_item_ids)

    monkeypatch.setattr(RetailService, "_changes", pause_first)
    second_started = Event()

    def submit(started: Event | None = None) -> str:
        with sessions() as session:
            try:
                if started is not None:
                    started.set()
                RetailService(session).exchange_delivered_order_items(
                    "delivered", [old_id], [new_id], "gift1", "u1"
                )
                return "ok"
            except ConflictError:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit)
        assert entered.wait(5)
        second = pool.submit(submit, second_started)
        assert second_started.wait(5)
        release.set()
        assert sorted([first.result(timeout=10), second.result(timeout=10)]) == [
            "conflict",
            "ok",
        ]
    with sessions() as session:
        order = session.get(Order, "delivered")
        assert order.status == "exchange requested"
        assert session.get(PaymentMethod, "gift1").balance == expected_balance
        assert len(order.exchange_items) == 1
        assert order.payment_history[-1].transaction_type == kind
        assert (
            session.scalar(
                select(func.count(OrderPayment.position)).where(
                    OrderPayment.order_id == "delivered"
                )
            )
            == 2
        )

"""HTTP routes use the retail service and keep order changes transactional."""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from services.commerce_service.app.db.session import get_session
from services.commerce_service.app.main import create_app
from services.commerce_service.app.models import (
    AfterSalesCase,
    Base,
    Order,
    OrderItem,
    OrderPayment,
    PaymentMethod,
    Product,
    User,
    Variant,
)


@pytest.fixture
def client_and_sessions(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'api.sqlite'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(
            Product(
                product_id="p1",
                name="Shirt",
                variants=[
                    Variant(
                        item_id="sku-a",
                        options_json='{"size":"S"}',
                        available=True,
                        price=Decimal("10.00"),
                    ),
                    Variant(
                        item_id="sku-b",
                        options_json='{"size":"M"}',
                        available=True,
                        price=Decimal("15.00"),
                    ),
                ],
            )
        )
        session.add_all(
            [
                User(
                    user_id="u1",
                    first_name="Ada",
                    last_name="Lin",
                    email="ada@example.com",
                    payment_methods=[
                        PaymentMethod(payment_method_id="card1", source="credit_card")
                    ],
                ),
                User(
                    user_id="u2",
                    first_name="Bob",
                    last_name="Chen",
                    email="bob@example.com",
                    payment_methods=[
                        PaymentMethod(payment_method_id="card2", source="credit_card")
                    ],
                ),
            ]
        )
    with sessions.begin() as session:
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
                            item_id="sku-a",
                            price=Decimal("10.00"),
                            options_json='{"size":"S"}',
                        )
                    ],
                    payment_history=[
                        OrderPayment(
                            position=0,
                            transaction_type="payment",
                            amount=Decimal("10.00"),
                            payment_method_id="card1",
                        )
                    ],
                )
            )

    def test_session():
        with sessions() as session, session.begin():
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = test_session
    yield (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ),
        sessions,
    )
    engine.dispose()


@pytest.mark.asyncio
async def test_retail_read_routes(client_and_sessions) -> None:
    client, _sessions = client_and_sessions
    async with client:
        products = await client.get("/products")
        assert products.json() == [{"product_id": "p1", "name": "Shirt"}]
        product = await client.get("/products/p1")
        assert product.status_code == 200
        assert [item["item_id"] for item in product.json()["variants"]] == [
            "sku-a",
            "sku-b",
        ]
        item = await client.get("/items/sku-a")
        assert item.json()["options"] == {"size": "S"}
        customer = await client.get("/customers/u1")
        assert customer.json()["first_name"] == "Ada"
        methods = await client.get("/customers/u1/payment-methods")
        assert [method["payment_method_id"] for method in methods.json()] == ["card1"]
        orders = await client.get("/customers/u1/orders")
        assert {order["order_id"] for order in orders.json()} == {
            "pending",
            "delivered",
        }
        order = await client.get("/orders/pending", params={"user_id": "u1"})
        assert order.json()["items"][0]["item_id"] == "sku-a"
        assert (
            await client.get("/orders/pending", params={"user_id": "u2"})
        ).status_code == 404
        assert (await client.get("/products/missing")).status_code == 404


@pytest.mark.asyncio
async def test_order_mutation_routes_and_errors(client_and_sessions) -> None:
    client, sessions = client_and_sessions
    async with client:
        invalid_method = await client.post(
            "/orders/pending/items/modify",
            json={
                "user_id": "u1",
                "item_ids": ["sku-a"],
                "new_item_ids": ["sku-b"],
                "payment_method_id": "card2",
            },
        )
        assert invalid_method.status_code == 404

        modified = await client.post(
            "/orders/pending/items/modify",
            json={
                "user_id": "u1",
                "item_ids": ["sku-a"],
                "new_item_ids": ["sku-b"],
                "payment_method_id": "card1",
            },
        )
        assert modified.status_code == 200
        assert modified.json()["status"] == "pending (item modified)"
        assert modified.json()["items"][0]["item_id"] == "sku-b"
        assert modified.json()["payment_history"][-1]["amount"] == "5.00"

        conflict = await client.post(
            "/orders/pending/items/modify",
            json={
                "user_id": "u1",
                "item_ids": ["sku-b"],
                "new_item_ids": ["sku-a"],
                "payment_method_id": "card1",
            },
        )
        assert conflict.status_code == 409

        returned = await client.post(
            "/orders/delivered/return",
            json={
                "user_id": "u1",
                "item_ids": ["sku-a"],
                "payment_method_id": "card1",
            },
        )
        assert returned.status_code == 200
        assert returned.json()["status"] == "return requested"
        assert returned.json()["return_estimated_refund"] == "10.00"

    with sessions() as session:
        assert session.scalar(
            select(Order).where(Order.order_id == "pending")
        ).status == ("pending (item modified)")
        assert session.scalar(
            select(Order).where(Order.order_id == "delivered")
        ).status == ("return requested")


@pytest.mark.asyncio
async def test_exchange_route_records_request(client_and_sessions) -> None:
    client, _sessions = client_and_sessions
    async with client:
        invalid = await client.post(
            "/orders/delivered/exchange",
            json={
                "user_id": "u1",
                "item_ids": ["sku-a"],
                "new_item_ids": [],
                "payment_method_id": "card1",
            },
        )
        assert invalid.status_code == 422

        exchanged = await client.post(
            "/orders/delivered/exchange",
            json={
                "user_id": "u1",
                "item_ids": ["sku-a"],
                "new_item_ids": ["sku-b"],
                "payment_method_id": "card1",
            },
        )
        assert exchanged.status_code == 200
        assert exchanged.json()["status"] == "exchange requested"
        assert exchanged.json()["exchange_items"] == [
            {"position": 0, "old_item_id": "sku-a", "new_item_id": "sku-b"}
        ]
        assert exchanged.json()["payment_history"][-1]["amount"] == "5.00"


@pytest.mark.asyncio
async def test_after_sales_case_lifecycle_and_customer_scope(
    client_and_sessions,
) -> None:
    client, sessions = client_and_sessions
    async with client:
        foreign_order = await client.post(
            "/after-sales/",
            json={
                "order_id": "delivered",
                "customer_id": "u2",
                "case_type": "RETURN_REFUND",
                "reason": "Wrong size",
            },
        )
        assert foreign_order.status_code == 404

        created = await client.post(
            "/after-sales/",
            json={
                "order_id": "delivered",
                "customer_id": "u1",
                "case_type": "RETURN_REFUND",
                "reason": "Wrong size",
            },
        )
        assert created.status_code == 201
        case = created.json()
        case_id = case["case_id"]
        assert case["status"] == "PROCESSING"
        assert case["resolution"] is None
        assert case["closed_at"] is None

        assert (
            await client.get(f"/after-sales/{case_id}", params={"customer_id": "u2"})
        ).status_code == 404
        listed = await client.get("/after-sales/", params={"customer_id": "u1"})
        assert [item["case_id"] for item in listed.json()] == [case_id]

        wrong_outcome = await client.post(
            f"/after-sales/{case_id}/close",
            json={"customer_id": "u1", "resolution": "EXCHANGED"},
        )
        assert wrong_outcome.status_code == 422
        closed = await client.post(
            f"/after-sales/{case_id}/close",
            json={"customer_id": "u1", "resolution": "REJECTED"},
        )
        assert closed.status_code == 200
        assert closed.json()["status"] == "CLOSED"
        assert closed.json()["resolution"] == "REJECTED"
        assert closed.json()["closed_at"] is not None
        assert (
            await client.post(
                f"/after-sales/{case_id}/close",
                json={"customer_id": "u1", "resolution": "REFUNDED"},
            )
        ).status_code == 409

    with sessions() as session:
        assert session.query(AfterSalesCase).count() == 1
        assert session.get(Order, "delivered").status == "delivered"
        assert len(session.get(Order, "delivered").payment_history) == 1

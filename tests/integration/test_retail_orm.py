"""Verify the retail ORM can persist its main relationships."""

from decimal import Decimal

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from services.commerce_service.app.models import (
    Base,
    FulfillmentItem,
    FulfillmentTracking,
    Order,
    OrderItem,
    OrderPayment,
    PaymentMethod,
    Product,
    User,
    Variant,
)


def test_retail_order_relationships() -> None:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    address = {"address1": "1 Main St", "city": "Shanghai", "country": "CN"}

    with Session(engine) as session:
        product = Product(
            product_id="p1",
            name="Shirt",
            variants=[Variant(item_id="sku1", options_json='{"size": "M"}', available=True, price=Decimal("25.00"))],
        )
        user = User(
            user_id="u1",
            first_name="Ada",
            last_name="Lin",
            **address,
            email="ada@example.com",
            payment_methods=[PaymentMethod(payment_method_id="cc1", source="credit_card", brand="Visa", last_four="1234")],
        )
        order = Order(
            order_id="o1",
            user=user,
            **address.copy(),
            status="processed",
            items=[OrderItem(position=0, name="Shirt", product_id="p1", item_id="sku1", price=Decimal("25.00"), options_json='{"size": "M"}')],
            payment_history=[OrderPayment(position=0, transaction_type="payment", amount=Decimal("25.00"), payment_method_id="cc1")],
            fulfillment_tracking=[FulfillmentTracking(fulfillment_index=0, position=0, tracking_id="track1")],
            fulfillment_items=[FulfillmentItem(fulfillment_index=0, position=0, item_id="sku1")],
        )
        session.add_all([product, user, order])
        session.commit()

    with Session(engine) as session:
        loaded = session.scalar(select(Order).where(Order.order_id == "o1"))
        assert loaded is not None
        assert loaded.user.email == "ada@example.com"
        assert loaded.items[0].price == Decimal("25.00")
        assert loaded.fulfillment_items[0].item_id == "sku1"
        assert loaded.fulfillment_tracking[0].tracking_id == "track1"
        assert loaded.payment_history[0].payment_method_id == "cc1"

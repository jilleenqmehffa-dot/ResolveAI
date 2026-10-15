"""SQLAlchemy queries and persistence for retail operations."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from services.commerce_service.app.models import (
    Order,
    OrderExchangeItem,
    OrderPayment,
    OrderReturnItem,
    PaymentMethod,
    Product,
    Variant,
)


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_product_types(self) -> list[tuple[str, str]]:
        statement = select(Product.product_id, Product.name).order_by(
            Product.name, Product.product_id
        )
        return [
            (product_id, name) for product_id, name in self.session.execute(statement)
        ]

    def get_with_variants(self, product_id: str) -> Product | None:
        statement = (
            select(Product)
            .where(Product.product_id == product_id)
            .options(selectinload(Product.variants))
        )
        return self.session.scalar(statement)

    def get_variant(self, item_id: str) -> Variant | None:
        statement = (
            select(Variant)
            .where(Variant.item_id == item_id)
            .options(selectinload(Variant.product))
        )
        return self.session.scalar(statement)


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_update(self, order_id: str) -> Order | None:
        statement = (
            select(Order)
            .where(Order.order_id == order_id)
            .options(selectinload(Order.items), selectinload(Order.payment_history))
            .with_for_update(of=Order)
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(statement)

    def save(self, order: Order) -> None:
        self.session.add(order)


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_payment_method(
        self, user_id: str, payment_method_id: str
    ) -> PaymentMethod | None:
        statement = (
            select(PaymentMethod)
            .where(
                PaymentMethod.payment_method_id == payment_method_id,
                PaymentMethod.user_id == user_id,
            )
            .with_for_update(of=PaymentMethod)
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(statement)


class PaymentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_transaction(
        self, order: Order, kind: str, amount: Decimal, payment_method_id: str
    ) -> None:
        order.payment_history.append(
            OrderPayment(
                position=max(
                    (payment.position for payment in order.payment_history), default=-1
                )
                + 1,
                transaction_type=kind,
                amount=amount,
                payment_method_id=payment_method_id,
            )
        )


class ExchangeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_request(self, order: Order, pairs: list[tuple[str, str]]) -> None:
        order.exchange_items.extend(
            OrderExchangeItem(position=position, old_item_id=original, new_item_id=new)
            for position, (original, new) in enumerate(
                pairs, start=len(order.exchange_items)
            )
        )


class ReturnRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_request(self, order: Order, item_ids: list[str]) -> None:
        order.return_items.extend(
            OrderReturnItem(position=position, item_id=item_id)
            for position, item_id in enumerate(item_ids, start=len(order.return_items))
        )

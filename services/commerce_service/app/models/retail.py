"""Retail tables matching the benchmark CSV business dataset."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.commerce_service.app.models.base import Base


class Product(Base):
    __tablename__ = "products"

    product_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    variants: Mapped[list[Variant]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class Variant(Base):
    __tablename__ = "product_variants"
    __table_args__ = (UniqueConstraint("product_id", "item_id"),)

    item_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.product_id"), nullable=False, index=True
    )
    options_json: Mapped[str] = mapped_column(Text, nullable=False)
    available: Mapped[bool] = mapped_column(nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    product: Mapped[Product] = relationship(back_populates="variants")


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    address1: Mapped[str | None] = mapped_column(Text)
    address2: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    zip: Mapped[str | None] = mapped_column(Text)
    payment_methods: Mapped[list[PaymentMethod]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    orders: Mapped[list[Order]] = relationship(back_populates="user")
    order_links: Mapped[list[UserOrderLink]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class PaymentMethod(Base):
    __tablename__ = "user_payment_methods"

    payment_method_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    brand: Mapped[str | None] = mapped_column(Text)
    last_four: Mapped[str | None] = mapped_column(Text)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    user: Mapped[User] = relationship(back_populates="payment_methods")


class UserOrderLink(Base):
    __tablename__ = "user_order_links"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(nullable=False)
    user: Mapped[User] = relationship(back_populates="order_links")


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    exchange_payment_method_id: Mapped[str | None] = mapped_column(Text)
    exchange_price_difference: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    return_payment_method_id: Mapped[str | None] = mapped_column(Text)
    address1: Mapped[str | None] = mapped_column(Text)
    address2: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    zip: Mapped[str | None] = mapped_column(Text)
    user: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItem.position",
    )
    fulfillment_tracking: Mapped[list[FulfillmentTracking]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="(FulfillmentTracking.fulfillment_index, FulfillmentTracking.position)",
    )
    fulfillment_items: Mapped[list[FulfillmentItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="(FulfillmentItem.fulfillment_index, FulfillmentItem.position)",
    )
    payment_history: Mapped[list[OrderPayment]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderPayment.position",
    )
    exchange_items: Mapped[list[OrderExchangeItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderExchangeItem.position",
    )
    return_items: Mapped[list[OrderReturnItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderReturnItem.position",
    )

    @property
    def return_estimated_refund(self) -> Decimal | None:
        if self.status != "return requested":
            return None
        remaining = list(self.items)
        total = Decimal("0.00")
        for returned in self.return_items:
            match = next(
                (item for item in remaining if item.item_id == returned.item_id), None
            )
            if match is not None:
                total += match.price
                remaining.remove(match)
        return total


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "item_id"],
            ["product_variants.product_id", "product_variants.item_id"],
        ),
    )

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    item_id: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    options_json: Mapped[str] = mapped_column(Text, nullable=False)
    order: Mapped[Order] = relationship(back_populates="items")


class FulfillmentTracking(Base):
    __tablename__ = "order_fulfillment_tracking"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id"), primary_key=True
    )
    fulfillment_index: Mapped[int] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    tracking_id: Mapped[str] = mapped_column(Text, nullable=False)
    order: Mapped[Order] = relationship(back_populates="fulfillment_tracking")


class FulfillmentItem(Base):
    __tablename__ = "order_fulfillment_items"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id"), primary_key=True
    )
    fulfillment_index: Mapped[int] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("product_variants.item_id"), nullable=False
    )
    order: Mapped[Order] = relationship(back_populates="fulfillment_items")


class OrderPayment(Base):
    __tablename__ = "order_payments"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    transaction_type: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method_id: Mapped[str] = mapped_column(
        ForeignKey("user_payment_methods.payment_method_id"), nullable=False
    )
    order: Mapped[Order] = relationship(back_populates="payment_history")


class OrderExchangeItem(Base):
    __tablename__ = "order_exchange_items"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    old_item_id: Mapped[str] = mapped_column(
        ForeignKey("product_variants.item_id"), nullable=False
    )
    new_item_id: Mapped[str] = mapped_column(
        ForeignKey("product_variants.item_id"), nullable=False
    )
    order: Mapped[Order] = relationship(back_populates="exchange_items")


class OrderReturnItem(Base):
    __tablename__ = "order_return_items"

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.order_id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("product_variants.item_id"), nullable=False
    )
    order: Mapped[Order] = relationship(back_populates="return_items")

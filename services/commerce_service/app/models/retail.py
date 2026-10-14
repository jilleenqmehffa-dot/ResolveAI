"""Relational models for the retail catalog, customers, and orders."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.commerce_service.app.models.base import Base


class Product(Base):
    __tablename__ = "products"

    product_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    variants: Mapped[list[Variant]] = relationship(back_populates="product", cascade="all, delete-orphan")


class Variant(Base):
    __tablename__ = "variants"
    __table_args__ = (
        UniqueConstraint("product_id", "item_id"),
        CheckConstraint("price >= 0", name="ck_variants_price_nonnegative"),
    )

    item_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.product_id"), nullable=False, index=True)
    options: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    available: Mapped[bool] = mapped_column(nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    product: Mapped[Product] = relationship(back_populates="variants")


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)

    payment_methods: Mapped[list[PaymentMethod]] = relationship(back_populates="user", cascade="all, delete-orphan")
    orders: Mapped[list[Order]] = relationship(back_populates="user")


class PaymentMethod(Base):
    __tablename__ = "payment_methods"
    __table_args__ = (
        CheckConstraint("source IN ('credit_card', 'paypal', 'gift_card')", name="ck_payment_methods_source"),
        CheckConstraint("balance IS NULL OR balance >= 0", name="ck_payment_methods_balance_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100))
    last_four: Mapped[str | None] = mapped_column(String(4))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    user: Mapped[User] = relationship(back_populates="payment_methods")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processed', 'pending', 'pending (item modified)', "
            "'delivered', 'cancelled', 'exchange requested', 'return requested')",
            name="ck_orders_status",
        ),
        CheckConstraint("cancel_reason IS NULL OR status = 'cancelled'", name="ck_orders_cancel_reason_status"),
    )

    order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    address: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cancel_reason: Mapped[str | None] = mapped_column(String(50))
    exchange_payment_method_id: Mapped[str | None] = mapped_column(ForeignKey("payment_methods.id"))
    exchange_price_difference: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    return_payment_method_id: Mapped[str | None] = mapped_column(ForeignKey("payment_methods.id"))
    return_estimated_refund: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    user: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(back_populates="order", cascade="all, delete-orphan")
    fulfillments: Mapped[list[Fulfillment]] = relationship(back_populates="order", cascade="all, delete-orphan")
    payment_history: Mapped[list[OrderPayment]] = relationship(back_populates="order", cascade="all, delete-orphan")
    exchange_items: Mapped[list[OrderExchangeItem]] = relationship(back_populates="order", cascade="all, delete-orphan")
    return_items: Mapped[list[OrderReturnItem]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        ForeignKeyConstraint(["product_id", "item_id"], ["variants.product_id", "variants.item_id"]),
        CheckConstraint("price >= 0", name="ck_order_items_price_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    item_id: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    options: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")


class Fulfillment(Base):
    __tablename__ = "fulfillments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), nullable=False, index=True)
    tracking_id: Mapped[list[str]] = mapped_column(JSON, nullable=False)

    order: Mapped[Order] = relationship(back_populates="fulfillments")
    items: Mapped[list[FulfillmentItem]] = relationship(back_populates="fulfillment", cascade="all, delete-orphan")


class FulfillmentItem(Base):
    __tablename__ = "fulfillment_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    fulfillment_id: Mapped[int] = mapped_column(ForeignKey("fulfillments.id"), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("variants.item_id"), nullable=False)

    fulfillment: Mapped[Fulfillment] = relationship(back_populates="items")


class OrderPayment(Base):
    __tablename__ = "order_payments"
    __table_args__ = (
        CheckConstraint("transaction_type IN ('payment', 'refund')", name="ck_order_payments_type"),
        CheckConstraint("amount > 0", name="ck_order_payments_amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), nullable=False, index=True)
    transaction_type: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method_id: Mapped[str] = mapped_column(ForeignKey("payment_methods.id"), nullable=False)

    order: Mapped[Order] = relationship(back_populates="payment_history")


class OrderExchangeItem(Base):
    __tablename__ = "order_exchange_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), nullable=False, index=True)
    original_item_id: Mapped[str] = mapped_column(ForeignKey("variants.item_id"), nullable=False)
    new_item_id: Mapped[str] = mapped_column(ForeignKey("variants.item_id"), nullable=False)

    order: Mapped[Order] = relationship(back_populates="exchange_items")


class OrderReturnItem(Base):
    __tablename__ = "order_return_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("variants.item_id"), nullable=False)

    order: Mapped[Order] = relationship(back_populates="return_items")

"""Catalog reads and transactional retail order changes."""

from collections import defaultdict, deque
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session, SessionTransaction

from services.commerce_service.app.core.exceptions import (
    ConflictError,
    InsufficientBalanceError,
    NotFoundError,
    ValidationError,
)
from services.commerce_service.app.models import (
    Order,
    OrderItem,
    PaymentMethod,
    Variant,
)
from services.commerce_service.app.repositories.retail import (
    ExchangeRepository,
    OrderRepository,
    PaymentRepository,
    ProductRepository,
    ReturnRepository,
    UserRepository,
)
from services.commerce_service.app.schemas.retail import (
    ItemDetails,
    ProductDetails,
    ProductType,
)

CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class RetailService:
    """Apply retail rules using a caller-provided synchronous Session."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.products = ProductRepository(session)
        self.orders = OrderRepository(session)
        self.users = UserRepository(session)
        self.payments = PaymentRepository(session)
        self.exchanges = ExchangeRepository(session)
        self.returns = ReturnRepository(session)

    def _transaction(self) -> SessionTransaction:
        """Use a savepoint when the caller already owns the Session transaction."""
        return (
            self.session.begin_nested()
            if self.session.in_transaction()
            else self.session.begin()
        )

    def list_all_product_types(self) -> list[ProductType]:
        """Return product names and IDs, sorted by name and then ID."""
        return [
            ProductType(product_id, name)
            for product_id, name in self.products.list_product_types()
        ]

    def get_product_details(self, product_id: str) -> ProductDetails:
        """Return one product and all its variants."""
        product = self.products.get_with_variants(product_id)
        if product is None:
            raise NotFoundError(f"Product {product_id} was not found")
        variants = tuple(
            self._item_details(variant, product.name)
            for variant in sorted(product.variants, key=lambda v: v.item_id)
        )
        return ProductDetails(product.product_id, product.name, variants)

    def get_item_details(self, item_id: str) -> ItemDetails:
        """Return one indexed SKU and its parent product."""
        variant = self.products.get_variant(item_id)
        if variant is None:
            raise NotFoundError(f"Item {item_id} was not found")
        return self._item_details(variant, variant.product.name)

    @staticmethod
    def _item_details(variant: Variant, product_name: str) -> ItemDetails:
        return ItemDetails(
            variant.item_id,
            variant.product_id,
            product_name,
            dict(variant.options),
            _money(variant.price),
            variant.available,
        )

    def _locked_order(self, order_id: str, user_id: str, expected_status: str) -> Order:
        order = self.orders.get_for_update(order_id)
        if order is None or order.user_id != user_id:
            raise NotFoundError(f"Order {order_id} was not found")
        if order.status != expected_status:
            raise ConflictError(f"Order {order_id} must be {expected_status}")
        return order

    def _payment_method(self, user_id: str, payment_method_id: str) -> PaymentMethod:
        method = self.users.get_payment_method(user_id, payment_method_id)
        if method is None:
            raise NotFoundError(f"Payment method {payment_method_id} was not found")
        return method

    @staticmethod
    def _selected_items(order: Order, item_ids: Sequence[str]) -> list[OrderItem]:
        if not item_ids:
            raise ValidationError("At least one item is required")
        available: dict[str, deque[OrderItem]] = defaultdict(deque)
        for item in sorted(order.items, key=lambda item: item.id):
            available[item.item_id].append(item)
        selected = []
        for item_id in item_ids:
            if not available[item_id]:
                raise ValidationError(
                    f"Item {item_id} is not present in the requested quantity"
                )
            selected.append(available[item_id].popleft())
        return selected

    def _changes(
        self, order: Order, item_ids: Sequence[str], new_item_ids: Sequence[str]
    ) -> tuple[list[tuple[OrderItem, Variant]], Decimal]:
        if len(item_ids) != len(new_item_ids):
            raise ValidationError("Old and new item counts must match")
        selected = self._selected_items(order, item_ids)
        variants: dict[str, Variant] = {}
        changes = []
        difference = Decimal("0.00")
        for old_item, new_id in zip(selected, new_item_ids, strict=True):
            if old_item.item_id == new_id:
                raise ValidationError("New item must differ from the original item")
            if new_id not in variants:
                variant = self.products.get_variant(new_id)
                if variant is None:
                    raise NotFoundError(f"Item {new_id} was not found")
                variants[new_id] = variant
            variant = variants[new_id]
            if variant.product_id != old_item.product_id:
                raise ValidationError("New item must belong to the same product")
            if not variant.available:
                raise ValidationError(f"Item {new_id} is not available")
            difference += variant.price - old_item.price
            changes.append((old_item, variant))
        return changes, _money(difference)

    def _settle_difference(
        self, order: Order, method: PaymentMethod, difference: Decimal
    ) -> None:
        if difference == 0:
            return
        if method.source == "gift_card":
            if method.balance is None:
                raise ValidationError("Gift card has no balance")
            if difference > 0 and method.balance < difference:
                raise InsufficientBalanceError("Gift card balance is insufficient")
            method.balance = _money(method.balance - difference)
        self.payments.add_transaction(
            order,
            "payment" if difference > 0 else "refund",
            abs(difference),
            method.id,
        )

    def modify_pending_order_items(
        self,
        order_id: str,
        item_ids: Sequence[str],
        new_item_ids: Sequence[str],
        payment_method_id: str,
        user_id: str,
    ) -> Order:
        """Replace pending items and settle their total price difference once."""
        with self._transaction():
            order = self._locked_order(order_id, user_id, "pending")
            changes, difference = self._changes(order, item_ids, new_item_ids)
            method = self._payment_method(user_id, payment_method_id)
            self._settle_difference(order, method, difference)
            for item, variant in changes:
                item.item_id = variant.item_id
                item.price = _money(variant.price)
                item.options = dict(variant.options)
            order.status = "pending (item modified)"
            self.orders.save(order)
        return order

    def exchange_delivered_order_items(
        self,
        order_id: str,
        item_ids: Sequence[str],
        new_item_ids: Sequence[str],
        payment_method_id: str,
        user_id: str,
    ) -> Order:
        """Submit one exchange request without replacing purchased items."""
        with self._transaction():
            order = self._locked_order(order_id, user_id, "delivered")
            changes, difference = self._changes(order, item_ids, new_item_ids)
            method = self._payment_method(user_id, payment_method_id)
            self._settle_difference(order, method, difference)
            order.status = "exchange requested"
            order.exchange_payment_method_id = method.id
            order.exchange_price_difference = difference
            self.exchanges.create_request(
                order, [(item.item_id, variant.item_id) for item, variant in changes]
            )
            self.orders.save(order)
        return order

    def return_delivered_order_items(
        self,
        order_id: str,
        item_ids: Sequence[str],
        payment_method_id: str,
        user_id: str,
    ) -> Order:
        """Submit one return request and estimate its refund without issuing it."""
        with self._transaction():
            order = self._locked_order(order_id, user_id, "delivered")
            selected = self._selected_items(order, item_ids)
            method = self._payment_method(user_id, payment_method_id)
            original_payments = [
                payment
                for payment in order.payment_history
                if payment.transaction_type == "payment"
            ]
            original_id = (
                min(original_payments, key=lambda payment: payment.id).payment_method_id
                if original_payments
                else None
            )
            if method.source != "gift_card" and method.id != original_id:
                raise ValidationError(
                    "Refund method must be the original payment method or a gift card"
                )
            order.status = "return requested"
            order.return_payment_method_id = method.id
            order.return_estimated_refund = _money(
                sum((item.price for item in selected), Decimal("0.00"))
            )
            self.returns.create_request(order, [item.item_id for item in selected])
            self.orders.save(order)
        return order

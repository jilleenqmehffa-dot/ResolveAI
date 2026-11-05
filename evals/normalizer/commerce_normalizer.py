"""Normalize Commerce HTTP snapshots without querying or judging them."""

from __future__ import annotations

from evals.adapter.commerce_adapter import CommerceSnapshot
from evals.normalizer.common import (
    normalize_customer,
    normalize_item,
    normalize_order,
    normalize_payment_method,
    normalize_product,
)
from evals.normalizer.models import NormalizedCommerce


class CommerceNormalizer:
    def normalize(self, snapshot: CommerceSnapshot) -> NormalizedCommerce:
        payment_methods = [
            normalize_payment_method(method) for method in snapshot.payment_methods
        ]
        order_summaries = [normalize_order(order) for order in snapshot.order_summaries]
        return NormalizedCommerce(
            task_id=snapshot.task_id,
            customer_id=snapshot.customer_id,
            captured_at=snapshot.captured_at,
            customer=normalize_customer(snapshot.customer),
            payment_methods={
                method["payment_method_id"]: method for method in payment_methods
            },
            order_summaries={order["order_id"]: order for order in order_summaries},
            orders={
                order_id: normalize_order(order, order_id=order_id)
                if order is not None
                else None
                for order_id, order in snapshot.orders.items()
            },
            products={
                product_id: normalize_product(product) if product is not None else None
                for product_id, product in snapshot.products.items()
            },
            items={
                item_id: normalize_item(item) if item is not None else None
                for item_id, item in snapshot.items.items()
            },
            identity_lookups={
                str(action_id): str(customer_id) if customer_id is not None else None
                for action_id, customer_id in snapshot.identity_lookups.items()
            },
        )

"""Order lookup, modification, exchange, and return routes."""

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from services.commerce_service.app.api.dependencies import (
    RetailServiceDep,
    SessionDep,
    trusted_agent_customer,
)
from services.commerce_service.app.core.exceptions import NotFoundError
from services.commerce_service.app.models import Order
from services.commerce_service.app.schemas.api import (
    AddressRequest,
    AddressResponse,
    CancelOrderRequest,
    ChangePaymentRequest,
    ExchangeItemResponse,
    FulfillmentResponse,
    OrderActionRequest,
    OrderItemResponse,
    OrderPaymentResponse,
    OrderResponse,
    ReplaceItemsRequest,
    ReturnItemResponse,
)

router = APIRouter(prefix="/orders", tags=["orders"])


def _order_response(order: Order) -> OrderResponse:
    fulfillment_indexes = sorted(
        {item.fulfillment_index for item in order.fulfillment_tracking}
        | {item.fulfillment_index for item in order.fulfillment_items}
    )
    return OrderResponse(
        order_id=order.order_id,
        user_id=order.user_id,
        status=order.status,
        address=AddressResponse.model_validate(
            {field: getattr(order, field) for field in AddressResponse.model_fields}
        ),
        cancel_reason=order.cancel_reason,
        exchange_payment_method_id=order.exchange_payment_method_id,
        exchange_price_difference=order.exchange_price_difference,
        return_payment_method_id=order.return_payment_method_id,
        return_estimated_refund=order.return_estimated_refund,
        items=[
            OrderItemResponse(
                position=item.position,
                name=item.name,
                product_id=item.product_id,
                item_id=item.item_id,
                price=item.price,
                options=json.loads(item.options_json),
            )
            for item in order.items
        ],
        payment_history=[
            OrderPaymentResponse(
                position=payment.position,
                transaction_type=payment.transaction_type,
                amount=payment.amount,
                payment_method_id=payment.payment_method_id,
            )
            for payment in order.payment_history
        ],
        fulfillments=[
            FulfillmentResponse(
                tracking_id=[
                    tracking.tracking_id
                    for tracking in order.fulfillment_tracking
                    if tracking.fulfillment_index == index
                ],
                item_ids=[
                    item.item_id
                    for item in order.fulfillment_items
                    if item.fulfillment_index == index
                ],
            )
            for index in fulfillment_indexes
        ],
        exchange_items=[
            ExchangeItemResponse(
                position=item.position,
                old_item_id=item.old_item_id,
                new_item_id=item.new_item_id,
            )
            for item in order.exchange_items
        ],
        return_items=[
            ReturnItemResponse(position=item.position, item_id=item.item_id)
            for item in order.return_items
        ],
    )


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: str,
    session: SessionDep,
    customer_id: str = Depends(trusted_agent_customer),
) -> OrderResponse:
    order = session.scalar(
        select(Order)
        .where(Order.order_id == order_id, Order.user_id == customer_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.payment_history),
            selectinload(Order.fulfillment_tracking),
            selectinload(Order.fulfillment_items),
            selectinload(Order.exchange_items),
            selectinload(Order.return_items),
        )
    )
    if order is None:
        raise NotFoundError(f"Order {order_id} was not found")
    return _order_response(order)


@router.post("/{order_id}/items/modify", response_model=OrderResponse)
def modify_order_items(
    order_id: str,
    request: ReplaceItemsRequest,
    service: RetailServiceDep,
    customer_id: str = Depends(trusted_agent_customer),
) -> OrderResponse:
    order = service.modify_pending_order_items(
        order_id,
        request.item_ids,
        request.new_item_ids,
        request.payment_method_id,
        customer_id,
    )
    return _order_response(order)


@router.post("/{order_id}/exchange", response_model=OrderResponse)
def exchange_order_items(
    order_id: str,
    request: ReplaceItemsRequest,
    service: RetailServiceDep,
    customer_id: str = Depends(trusted_agent_customer),
) -> OrderResponse:
    order = service.exchange_delivered_order_items(
        order_id,
        request.item_ids,
        request.new_item_ids,
        request.payment_method_id,
        customer_id,
    )
    return _order_response(order)


@router.post("/{order_id}/return", response_model=OrderResponse)
def return_order_items(
    order_id: str,
    request: OrderActionRequest,
    service: RetailServiceDep,
    customer_id: str = Depends(trusted_agent_customer),
) -> OrderResponse:
    order = service.return_delivered_order_items(
        order_id,
        request.item_ids,
        request.payment_method_id,
        customer_id,
    )
    return _order_response(order)


@router.post("/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(
    order_id: str,
    request: CancelOrderRequest,
    service: RetailServiceDep,
    customer_id: str = Depends(trusted_agent_customer),
) -> OrderResponse:
    return _order_response(
        service.cancel_pending_order(order_id, request.reason, customer_id)
    )


@router.post("/{order_id}/address", response_model=OrderResponse)
def modify_order_address(
    order_id: str,
    request: AddressRequest,
    service: RetailServiceDep,
    customer_id: str = Depends(trusted_agent_customer),
) -> OrderResponse:
    return _order_response(
        service.modify_pending_order_address(
            order_id, request.model_dump(), customer_id
        )
    )


@router.post("/{order_id}/payment", response_model=OrderResponse)
def modify_order_payment(
    order_id: str,
    request: ChangePaymentRequest,
    service: RetailServiceDep,
    customer_id: str = Depends(trusted_agent_customer),
) -> OrderResponse:
    return _order_response(
        service.modify_pending_order_payment(
            order_id, request.payment_method_id, customer_id
        )
    )

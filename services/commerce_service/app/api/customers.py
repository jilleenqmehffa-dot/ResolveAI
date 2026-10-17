"""Customer profile, payment method, and order lookup routes."""

from fastapi import APIRouter
from sqlalchemy import select

from services.commerce_service.app.api.dependencies import SessionDep
from services.commerce_service.app.core.exceptions import NotFoundError
from services.commerce_service.app.models import Order, PaymentMethod, User
from services.commerce_service.app.schemas.api import (
    AddressResponse,
    CustomerResponse,
    OrderSummaryResponse,
    PaymentMethodResponse,
)

router = APIRouter(prefix="/customers", tags=["customers"])


def _customer(session: SessionDep, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"Customer {user_id} was not found")
    return user


@router.get("/{user_id}", response_model=CustomerResponse)
def get_customer(user_id: str, session: SessionDep) -> CustomerResponse:
    user = _customer(session, user_id)
    return CustomerResponse(
        user_id=user.user_id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        address=AddressResponse.model_validate(
            {field: getattr(user, field) for field in AddressResponse.model_fields}
        ),
    )


@router.get("/{user_id}/payment-methods", response_model=list[PaymentMethodResponse])
def list_payment_methods(
    user_id: str, session: SessionDep
) -> list[PaymentMethodResponse]:
    _customer(session, user_id)
    methods = session.scalars(
        select(PaymentMethod)
        .where(PaymentMethod.user_id == user_id)
        .order_by(PaymentMethod.payment_method_id)
    )
    return [
        PaymentMethodResponse(
            payment_method_id=method.payment_method_id,
            source=method.source,
            brand=method.brand,
            last_four=method.last_four,
            balance=method.balance,
        )
        for method in methods
    ]


@router.get("/{user_id}/orders", response_model=list[OrderSummaryResponse])
def list_customer_orders(
    user_id: str, session: SessionDep
) -> list[OrderSummaryResponse]:
    _customer(session, user_id)
    orders = session.scalars(
        select(Order).where(Order.user_id == user_id).order_by(Order.order_id)
    )
    return [
        OrderSummaryResponse(
            order_id=order.order_id, user_id=order.user_id, status=order.status
        )
        for order in orders
    ]

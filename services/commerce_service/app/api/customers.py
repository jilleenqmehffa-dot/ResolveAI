"""Customer profile, payment method, and order lookup routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from services.commerce_service.app.api.dependencies import (
    SessionDep,
    trusted_agent_token,
)
from services.commerce_service.app.core.exceptions import NotFoundError
from services.commerce_service.app.models import Order, PaymentMethod, User
from services.commerce_service.app.schemas.api import (
    AddressResponse,
    CustomerIdResponse,
    CustomerResponse,
    OrderSummaryResponse,
    PaymentMethodResponse,
)

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("/lookup/by-email", response_model=CustomerIdResponse)
def find_customer_by_email(
    session: SessionDep,
    email: str = Query(min_length=1),
    _trusted: None = Depends(trusted_agent_token),
) -> CustomerIdResponse:
    """Resolve a Bench customer from an email clue for trusted callers."""
    user_id = session.scalar(
        select(User.user_id).where(
            func.lower(User.email) == email.strip().lower(),
        )
    )
    if user_id is None:
        raise NotFoundError("Customer was not found")
    return CustomerIdResponse(user_id=user_id)


@router.get("/lookup/by-name-zip", response_model=CustomerIdResponse)
def find_customer_by_name_zip(
    session: SessionDep,
    first_name: str = Query(min_length=1),
    last_name: str = Query(min_length=1),
    zip: str = Query(min_length=1),
    _trusted: None = Depends(trusted_agent_token),
) -> CustomerIdResponse:
    """Resolve a Bench customer from name and ZIP clues for trusted callers."""
    user_id = session.scalar(
        select(User.user_id).where(
            func.lower(User.first_name) == first_name.strip().lower(),
            func.lower(User.last_name) == last_name.strip().lower(),
            User.zip == zip.strip(),
        )
    )
    if user_id is None:
        raise NotFoundError("Customer was not found")
    return CustomerIdResponse(user_id=user_id)


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

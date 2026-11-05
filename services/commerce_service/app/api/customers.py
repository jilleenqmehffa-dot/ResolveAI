"""Customer profile, payment method, and order lookup routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select

from services.commerce_service.app.api.dependencies import (
    RetailServiceDep,
    SessionDep,
    trusted_agent_customer,
    trusted_agent_token,
)
from services.commerce_service.app.core.exceptions import NotFoundError
from services.commerce_service.app.models import (
    Order,
    PaymentMethod,
    User,
    UserOrderLink,
)
from services.commerce_service.app.schemas.api import (
    AddressRequest,
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


def _customer_response(user: User) -> CustomerResponse:
    linked_ids = [link.order_id for link in user.order_links]
    linked = set(linked_ids)
    return CustomerResponse(
        user_id=user.user_id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        address=AddressResponse.model_validate(
            {field: getattr(user, field) for field in AddressResponse.model_fields}
        ),
        orders=linked_ids + sorted(
            order.order_id for order in user.orders if order.order_id not in linked
        ),
        payment_methods=[
            PaymentMethodResponse(
                payment_method_id=method.payment_method_id,
                source=method.source,
                brand=method.brand,
                last_four=method.last_four,
                balance=method.balance,
            )
            for method in sorted(
                user.payment_methods, key=lambda method: method.payment_method_id
            )
        ],
    )


@router.get("/{user_id}", response_model=CustomerResponse)
def get_customer(user_id: str, session: SessionDep) -> CustomerResponse:
    return _customer_response(_customer(session, user_id))


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
    orders = session.execute(
        select(Order, UserOrderLink.position)
        .outerjoin(
            UserOrderLink,
            and_(
                UserOrderLink.order_id == Order.order_id,
                UserOrderLink.user_id == Order.user_id,
            ),
        )
        .where(Order.user_id == user_id)
        .order_by(UserOrderLink.position.asc().nulls_last(), Order.order_id)
    )
    return [
        OrderSummaryResponse(
            order_id=order.order_id,
            user_id=order.user_id,
            status=order.status,
            order_position=position,
        )
        for order, position in orders
    ]


@router.post("/{user_id}/address", response_model=CustomerResponse)
def modify_customer_address(
    user_id: str,
    request: AddressRequest,
    service: RetailServiceDep,
    customer_id: str = Depends(trusted_agent_customer),
) -> CustomerResponse:
    if user_id != customer_id:
        raise NotFoundError(f"Customer {user_id} was not found")
    user = service.modify_user_address(user_id, request.model_dump())
    return _customer_response(user)

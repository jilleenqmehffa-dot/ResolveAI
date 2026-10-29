"""Create, inspect, and close customer-owned after-sales cases."""

from datetime import UTC, datetime
from hmac import compare_digest
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from services.commerce_service.app.api.dependencies import (
    AfterSalesServiceDep,
    SessionDep,
)
from services.commerce_service.app.core.config import get_settings
from services.commerce_service.app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from services.commerce_service.app.models import AfterSalesCase, Order

router = APIRouter(prefix="/after-sales", tags=["after-sales"])

CaseType = Literal["REFUND_ONLY", "RETURN_REFUND", "EXCHANGE"]
Resolution = Literal["REFUNDED", "EXCHANGED", "REJECTED", "CANCELLED"]


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    case_type: CaseType
    reason: str = Field(min_length=1)


class CloseCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1)
    resolution: Resolution


class CaseResponse(BaseModel):
    case_id: UUID
    order_id: str
    customer_id: str
    case_type: CaseType
    status: Literal["PROCESSING", "CLOSED"]
    resolution: Resolution | None
    reason: str
    created_at: datetime
    closed_at: datetime | None


def _trusted_agent_customer(
    x_agent_token: str = Header(), x_customer_id: str = Header(min_length=1)
) -> str:
    expected = get_settings().agent_internal_token
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Agent token is not configured"
        )
    if not compare_digest(x_agent_token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Agent token")
    return x_customer_id


def _owned_case(session: SessionDep, case_id: UUID, customer_id: str) -> AfterSalesCase:
    case = session.scalar(
        select(AfterSalesCase)
        .where(
            AfterSalesCase.case_id == case_id, AfterSalesCase.customer_id == customer_id
        )
        .with_for_update()
    )
    if case is None:
        raise NotFoundError(f"After-sales case {case_id} was not found")
    return case


@router.post("/", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
def create_case(request: CreateCaseRequest, session: SessionDep) -> AfterSalesCase:
    order = session.scalar(
        select(Order).where(
            Order.order_id == request.order_id, Order.user_id == request.customer_id
        )
    )
    if order is None:
        raise NotFoundError(f"Order {request.order_id} was not found")
    case = AfterSalesCase(
        order_id=order.order_id,
        customer_id=request.customer_id,
        case_type=request.case_type,
        reason=request.reason.strip(),
        status="PROCESSING",
    )
    if not case.reason:
        raise ValidationError("Reason must not be blank")
    session.add(case)
    session.flush()
    return case


@router.get("/", response_model=list[CaseResponse])
def list_cases(
    session: SessionDep, customer_id: str = Query(min_length=1)
) -> list[AfterSalesCase]:
    return list(
        session.scalars(
            select(AfterSalesCase)
            .where(AfterSalesCase.customer_id == customer_id)
            .order_by(AfterSalesCase.created_at.desc(), AfterSalesCase.case_id.desc())
        )
    )


@router.get("/{case_id}", response_model=CaseResponse)
def get_case(
    case_id: UUID, session: SessionDep, customer_id: str = Query(min_length=1)
) -> AfterSalesCase:
    return _owned_case(session, case_id, customer_id)


@router.post("/{case_id}/close", response_model=CaseResponse)
def close_case(
    case_id: UUID, request: CloseCaseRequest, session: SessionDep
) -> AfterSalesCase:
    case = _owned_case(session, case_id, request.customer_id)
    if case.status != "PROCESSING":
        raise ConflictError(f"After-sales case {case_id} is already closed")
    expected_type = "EXCHANGE" if request.resolution == "EXCHANGED" else None
    if expected_type and case.case_type != expected_type:
        raise ValidationError("EXCHANGED resolution requires an exchange case")
    if request.resolution == "REFUNDED" and case.case_type == "EXCHANGE":
        raise ValidationError("REFUNDED resolution requires a refund case")
    case.status = "CLOSED"
    case.resolution = request.resolution
    case.closed_at = datetime.now(UTC)
    session.flush()
    return case


@router.put("/{case_id}/resolution/refunded", response_model=CaseResponse)
def mark_case_refunded(
    case_id: UUID,
    service: AfterSalesServiceDep,
    customer_id: str = Depends(_trusted_agent_customer),
) -> AfterSalesCase:
    return service.mark_refunded(case_id, customer_id)

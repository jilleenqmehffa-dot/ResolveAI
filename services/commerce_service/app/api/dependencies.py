"""Dependencies shared by retail routes."""

from hmac import compare_digest
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from services.commerce_service.app.core.config import get_settings
from services.commerce_service.app.db.session import get_session
from services.commerce_service.app.services.after_sales_service import AfterSalesService
from services.commerce_service.app.services.retail_service import RetailService


def get_retail_service(
    session: Annotated[Session, Depends(get_session)],
) -> RetailService:
    return RetailService(session)


RetailServiceDep = Annotated[RetailService, Depends(get_retail_service)]
SessionDep = Annotated[Session, Depends(get_session)]


def get_after_sales_service(session: SessionDep) -> AfterSalesService:
    return AfterSalesService(session)


AfterSalesServiceDep = Annotated[AfterSalesService, Depends(get_after_sales_service)]


def trusted_agent_token(x_agent_token: str = Header()) -> None:
    """Allow only callers that know the Commerce internal Agent token."""
    expected = get_settings().agent_internal_token
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Agent token is not configured"
        )
    if not compare_digest(x_agent_token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Agent token")


def trusted_agent_customer(
    x_customer_id: str = Header(min_length=1),
    _trusted: None = Depends(trusted_agent_token),
) -> str:
    """Return the customer identity supplied by a trusted internal caller."""
    return x_customer_id

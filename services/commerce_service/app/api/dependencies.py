"""Dependencies shared by retail routes."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from services.commerce_service.app.db.session import get_session
from services.commerce_service.app.services.retail_service import RetailService


def get_retail_service(
    session: Annotated[Session, Depends(get_session)],
) -> RetailService:
    return RetailService(session)


RetailServiceDep = Annotated[RetailService, Depends(get_retail_service)]
SessionDep = Annotated[Session, Depends(get_session)]

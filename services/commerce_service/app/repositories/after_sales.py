"""Persistence operations for customer-owned after-sales cases."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.commerce_service.app.models import AfterSalesCase


class AfterSalesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_owned_for_update(
        self, case_id: UUID, customer_id: str
    ) -> AfterSalesCase | None:
        return self.session.scalar(
            select(AfterSalesCase)
            .where(
                AfterSalesCase.case_id == case_id,
                AfterSalesCase.customer_id == customer_id,
            )
            .with_for_update(of=AfterSalesCase)
            .execution_options(populate_existing=True)
        )

    def save(self, case: AfterSalesCase) -> None:
        self.session.add(case)

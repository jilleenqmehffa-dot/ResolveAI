"""Business transitions for after-sales cases."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from services.commerce_service.app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from services.commerce_service.app.models import AfterSalesCase
from services.commerce_service.app.repositories.after_sales import AfterSalesRepository


class AfterSalesService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.cases = AfterSalesRepository(session)

    def mark_refunded(self, case_id: UUID, customer_id: str) -> AfterSalesCase:
        """Idempotently close a refund-type case with a REFUNDED resolution.

        This records the case outcome. It does not move money or prove payout.
        The request-scoped Session dependency owns the transaction.
        """
        case = self.cases.get_owned_for_update(case_id, customer_id)
        if case is None:
            raise NotFoundError(f"After-sales case {case_id} was not found")
        if case.case_type not in ("REFUND_ONLY", "RETURN_REFUND"):
            raise ValidationError("Only refund-type cases can be marked refunded")
        if case.status == "CLOSED":
            if case.resolution == "REFUNDED":
                return case
            raise ConflictError(f"After-sales case {case_id} is already closed")
        if case.status != "PROCESSING":
            raise ConflictError(f"After-sales case {case_id} is not processing")
        case.status = "CLOSED"
        case.resolution = "REFUNDED"
        case.closed_at = datetime.now(UTC)
        self.cases.save(case)
        self.session.flush()
        return case

"""Customer-visible after-sales cases, separate from order and payment facts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from services.commerce_service.app.models.base import Base


class AfterSalesCase(Base):
    __tablename__ = "after_sales_cases"
    __table_args__ = (
        CheckConstraint(
            "case_type IN ('REFUND_ONLY', 'RETURN_REFUND', 'EXCHANGE')",
            name="ck_after_sales_cases_type",
        ),
        CheckConstraint(
            "status IN ('PROCESSING', 'CLOSED')",
            name="ck_after_sales_cases_status",
        ),
        CheckConstraint(
            "resolution IS NULL OR resolution IN "
            "('REFUNDED', 'EXCHANGED', 'REJECTED', 'CANCELLED')",
            name="ck_after_sales_cases_resolution",
        ),
        CheckConstraint(
            "(status = 'PROCESSING' AND resolution IS NULL AND closed_at IS NULL) "
            "OR (status = 'CLOSED' AND resolution IS NOT NULL AND closed_at IS NOT NULL)",
            name="ck_after_sales_cases_lifecycle",
        ),
        Index("ix_after_sales_cases_customer_created", "customer_id", "created_at"),
    )

    case_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    order_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("orders.order_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    case_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PROCESSING"
    )
    resolution: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

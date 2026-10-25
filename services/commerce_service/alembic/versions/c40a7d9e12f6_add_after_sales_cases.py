"""Add independent after-sales cases with two lifecycle states.

Revision ID: c40a7d9e12f6
Revises: 8c2f0a4d91b7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c40a7d9e12f6"
down_revision: str | Sequence[str] | None = "8c2f0a4d91b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "after_sales_cases",
        sa.Column("case_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            sa.String(100),
            sa.ForeignKey("orders.order_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.String(100),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("case_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("resolution", sa.String(20)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "case_type IN ('REFUND_ONLY', 'RETURN_REFUND', 'EXCHANGE')",
            name="ck_after_sales_cases_type",
        ),
        sa.CheckConstraint(
            "status IN ('PROCESSING', 'CLOSED')", name="ck_after_sales_cases_status"
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('REFUNDED', 'EXCHANGED', 'REJECTED', 'CANCELLED')",
            name="ck_after_sales_cases_resolution",
        ),
        sa.CheckConstraint(
            "(status = 'PROCESSING' AND resolution IS NULL AND closed_at IS NULL) OR (status = 'CLOSED' AND resolution IS NOT NULL AND closed_at IS NOT NULL)",
            name="ck_after_sales_cases_lifecycle",
        ),
    )
    op.create_index("ix_after_sales_cases_order_id", "after_sales_cases", ["order_id"])
    op.create_index(
        "ix_after_sales_cases_customer_created",
        "after_sales_cases",
        ["customer_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_after_sales_cases_customer_created", table_name="after_sales_cases"
    )
    op.drop_index("ix_after_sales_cases_order_id", table_name="after_sales_cases")
    op.drop_table("after_sales_cases")

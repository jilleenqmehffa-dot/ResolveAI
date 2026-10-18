"""Store the estimated refund when a return is requested.

Revision ID: 6f2c9d1a4b70
Revises: 1d3a8191a431
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f2c9d1a4b70"
down_revision: str | Sequence[str] | None = "1d3a8191a431"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("return_estimated_refund", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "return_estimated_refund")

"""Persist the customer discovered by the Agent in anonymous evaluation sessions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7a4e93b1c2d0"
down_revision: str | Sequence[str] | None = "f3c19a7d4e82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("verified_customer_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "verified_customer_id")

"""Persist the model backend selected for each Agent run.

Revision ID: d6a51f72c9e0
Revises: c4e2a71d90b6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6a51f72c9e0"
down_revision: str | Sequence[str] | None = "c4e2a71d90b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("llm_backend", sa.String(16), nullable=False, server_default="deepseek"),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "llm_backend")

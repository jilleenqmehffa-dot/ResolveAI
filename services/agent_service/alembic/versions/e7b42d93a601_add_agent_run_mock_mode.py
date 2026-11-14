"""Persist per-execution Mock LLM mode for Celery workers."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b42d93a601"
down_revision: str | Sequence[str] | None = "d6a51f72c9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("mock_mode", sa.String(16), nullable=False, server_default="fast"),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "mock_mode")

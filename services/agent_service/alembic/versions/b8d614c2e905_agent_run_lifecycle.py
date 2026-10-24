"""add durable read-only Agent run lifecycle

Revision ID: b8d614c2e905
Revises: a1f3c6d8e920
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d614c2e905"
down_revision: str | Sequence[str] | None = "a1f3c6d8e920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL enum additions must commit before they can be used by updates.
    with op.get_context().autocommit_block():
        for value in ("QUEUED", "RETRY_WAIT", "TIMED_OUT"):
            op.execute(f"ALTER TYPE agent_run_status ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column("agent_runs", sa.Column("idempotency_key", sa.String(255)))
    op.add_column(
        "agent_runs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("agent_runs", sa.Column("lease_token", sa.String(36)))
    op.add_column(
        "agent_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True))
    )
    op.add_column("agent_runs", sa.Column("retry_at", sa.DateTime(timezone=True)))
    op.add_column("agent_runs", sa.Column("deadline_at", sa.DateTime(timezone=True)))
    op.add_column("agent_runs", sa.Column("result_text", sa.Text()))
    op.add_column("agent_runs", sa.Column("error_code", sa.String(255)))
    op.add_column(
        "agent_runs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_unique_constraint(
        "uq_agent_runs_conversation_key",
        "agent_runs",
        ["conversation_id", "idempotency_key"],
    )
    op.create_index(
        "ix_agent_runs_status_retry_at", "agent_runs", ["status", "retry_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_status_retry_at", table_name="agent_runs")
    op.drop_constraint("uq_agent_runs_conversation_key", "agent_runs", type_="unique")
    for name in (
        "updated_at",
        "error_code",
        "result_text",
        "deadline_at",
        "retry_at",
        "lease_expires_at",
        "lease_token",
        "version",
        "attempt_count",
        "idempotency_key",
    ):
        op.drop_column("agent_runs", name)
    # PostgreSQL cannot remove enum values without rebuilding the type. The old
    # enum remains harmless when the added values are no longer referenced.

"""Remove unused cross-conversation customer memories."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c19a7d4e82"
down_revision: str | Sequence[str] | None = "e7b42d93a601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

memory_type = postgresql.ENUM(
    "PREFERENCE", "PROFILE", "OTHER", name="agent_memory_type", create_type=False
)


def upgrade() -> None:
    op.drop_table("customer_memories")
    memory_type.drop(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    memory_type.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "customer_memories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.String(length=255), nullable=False),
        sa.Column("memory_type", memory_type, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_message_id", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_customer_memories_customer_id"),
        "customer_memories",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_customer_memories_customer_id_memory_type",
        "customer_memories",
        ["customer_id", "memory_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customer_memories_source_message_id"),
        "customer_memories",
        ["source_message_id"],
        unique=False,
    )

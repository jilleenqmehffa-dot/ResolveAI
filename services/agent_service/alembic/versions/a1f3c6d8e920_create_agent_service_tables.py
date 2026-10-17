"""create agent service tables

Revision ID: a1f3c6d8e920
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

conversation_status = postgresql.ENUM(
    "ACTIVE",
    "WAITING_HUMAN",
    "RESOLVED",
    "CLOSED",
    name="agent_conversation_status",
    create_type=False,
)
message_role = postgresql.ENUM(
    "USER", "ASSISTANT", name="agent_message_role", create_type=False
)
run_status = postgresql.ENUM(
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    name="agent_run_status",
    create_type=False,
)
memory_type = postgresql.ENUM(
    "PREFERENCE", "PROFILE", "OTHER", name="agent_memory_type", create_type=False
)
tool_status = postgresql.ENUM(
    "PENDING",
    "RUNNING",
    "RETRY_PENDING",
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN",
    name="agent_tool_execution_status",
    create_type=False,
)

revision: str = "a1f3c6d8e920"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for enum_type in (
        conversation_status,
        message_role,
        run_status,
        memory_type,
        tool_status,
    ):
        enum_type.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "conversations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status", conversation_status, server_default="ACTIVE", nullable=False
        ),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversations_customer_id"),
        "conversations",
        ["customer_id"],
        unique=False,
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", message_role, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "id", name="uq_messages_conversation_id_id"
        ),
    )
    op.create_index(
        op.f("ix_messages_conversation_id"),
        "messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("trigger_message_id", sa.UUID(), nullable=False),
        sa.Column("status", run_status, server_default="PENDING", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id", "trigger_message_id"],
            ["messages.conversation_id", "messages.id"],
            name="fk_agent_runs_trigger_same_conversation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_runs_conversation_id"),
        "agent_runs",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_runs_trigger_message_id"),
        "agent_runs",
        ["trigger_message_id"],
        unique=False,
    )
    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("last_message_id", sa.UUID(), nullable=True),
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
            ["conversation_id", "last_message_id"],
            ["messages.conversation_id", "messages.id"],
            name="fk_summaries_last_message_same_conversation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id"),
    )
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
    op.create_table(
        "tool_executions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("status", tool_status, server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column(
            "request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("error_code", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_tool_executions_attempt_count"
        ),
        sa.CheckConstraint("max_attempts > 0", name="ck_tool_executions_max_attempts"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "tool_call_id", name="uq_tool_executions_run_call"
        ),
    )
    op.create_index(
        op.f("ix_tool_executions_run_id"), "tool_executions", ["run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tool_executions_run_id"), table_name="tool_executions")
    op.drop_table("tool_executions")
    op.drop_index(
        op.f("ix_customer_memories_source_message_id"), table_name="customer_memories"
    )
    op.drop_index(
        "ix_customer_memories_customer_id_memory_type", table_name="customer_memories"
    )
    op.drop_index(
        op.f("ix_customer_memories_customer_id"), table_name="customer_memories"
    )
    op.drop_table("customer_memories")
    op.drop_table("conversation_summaries")
    op.drop_index(op.f("ix_agent_runs_trigger_message_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_conversation_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_conversations_customer_id"), table_name="conversations")
    op.drop_table("conversations")
    for enum_type in (
        tool_status,
        memory_type,
        run_status,
        message_role,
        conversation_status,
    ):
        enum_type.drop(op.get_bind(), checkfirst=False)

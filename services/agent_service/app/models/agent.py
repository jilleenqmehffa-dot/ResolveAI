"""Agent conversation, memory, run, and tool execution records."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.commerce_service.app.models.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class ConversationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WAITING_HUMAN = "WAITING_HUMAN"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class MessageRole(str, Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class MemoryType(str, Enum):
    PREFERENCE = "PREFERENCE"
    PROFILE = "PROFILE"
    OTHER = "OTHER"


class AgentRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ToolExecutionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY_PENDING = "RETRY_PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


def db_enum(enum: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum,
        name=name,
        values_callable=lambda members: [item.value for item in members],
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    customer_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[ConversationStatus] = mapped_column(
        db_enum(ConversationStatus, "agent_conversation_status"),
        nullable=False,
        default=ConversationStatus.ACTIVE,
        server_default="ACTIVE",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        foreign_keys="Message.conversation_id",
        passive_deletes=True,
    )
    summary: Mapped[ConversationSummary | None] = relationship(
        back_populates="conversation",
        foreign_keys="ConversationSummary.conversation_id",
        uselist=False,
        passive_deletes=True,
    )
    agent_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="conversation",
        foreign_keys="AgentRun.conversation_id",
        passive_deletes=True,
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "id", name="uq_messages_conversation_id_id"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        db_enum(MessageRole, "agent_message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )

    conversation: Mapped[Conversation] = relationship(
        back_populates="messages", foreign_keys=[conversation_id]
    )
    triggered_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="trigger_message",
        foreign_keys="AgentRun.trigger_message_id",
        primaryjoin="Message.id == AgentRun.trigger_message_id",
        passive_deletes=True,
    )
    covered_summaries: Mapped[list[ConversationSummary]] = relationship(
        back_populates="last_message",
        foreign_keys="ConversationSummary.last_message_id",
        primaryjoin="Message.id == ConversationSummary.last_message_id",
        passive_deletes=True,
    )
    sourced_memories: Mapped[list[CustomerMemory]] = relationship(
        back_populates="source_message",
        foreign_keys="CustomerMemory.source_message_id",
        passive_deletes=True,
    )


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "last_message_id"],
            ["messages.conversation_id", "messages.id"],
            name="fk_summaries_last_message_same_conversation",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    last_message_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )

    conversation: Mapped[Conversation] = relationship(
        back_populates="summary", foreign_keys=[conversation_id]
    )
    last_message: Mapped[Message | None] = relationship(
        back_populates="covered_summaries",
        foreign_keys=[last_message_id],
        primaryjoin="ConversationSummary.last_message_id == Message.id",
    )


class CustomerMemory(Base):
    __tablename__ = "customer_memories"
    __table_args__ = (
        Index(
            "ix_customer_memories_customer_id_memory_type", "customer_id", "memory_type"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    customer_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    memory_type: Mapped[MemoryType] = mapped_column(
        db_enum(MemoryType, "agent_memory_type"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )

    source_message: Mapped[Message | None] = relationship(
        back_populates="sourced_memories", foreign_keys=[source_message_id]
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "trigger_message_id"],
            ["messages.conversation_id", "messages.id"],
            name="fk_agent_runs_trigger_same_conversation",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trigger_message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        db_enum(AgentRunStatus, "agent_run_status"),
        nullable=False,
        default=AgentRunStatus.PENDING,
        server_default="PENDING",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    conversation: Mapped[Conversation] = relationship(
        back_populates="agent_runs", foreign_keys=[conversation_id]
    )
    trigger_message: Mapped[Message] = relationship(
        back_populates="triggered_runs",
        foreign_keys=[trigger_message_id],
        primaryjoin="AgentRun.trigger_message_id == Message.id",
    )
    tool_executions: Mapped[list[ToolExecution]] = relationship(
        back_populates="agent_run", passive_deletes=True
    )


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_tool_executions_run_call"),
        CheckConstraint("attempt_count >= 0", name="ck_tool_executions_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_tool_executions_max_attempts"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ToolExecutionStatus] = mapped_column(
        db_enum(ToolExecutionStatus, "agent_tool_execution_status"),
        nullable=False,
        default=ToolExecutionStatus.PENDING,
        server_default="PENDING",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    agent_run: Mapped[AgentRun] = relationship(back_populates="tool_executions")

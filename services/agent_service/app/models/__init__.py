"""Agent ORM models and their isolated migration metadata view."""

from sqlalchemy import MetaData

from services.agent_service.app.models.agent import (
    AgentRun,
    AgentRunStatus,
    Conversation,
    ConversationStatus,
    ConversationSummary,
    CustomerMemory,
    MemoryType,
    Message,
    MessageRole,
    ToolExecution,
    ToolExecutionStatus,
)
from services.commerce_service.app.models.base import Base

AGENT_TABLES = frozenset(
    {
        "conversations",
        "messages",
        "conversation_summaries",
        "customer_memories",
        "agent_runs",
        "tool_executions",
    }
)

# The existing Base also registers Commerce tables. Copy only Agent tables for
# Alembic autogeneration, so the two databases never share a migration target.
agent_metadata = MetaData()
for name in AGENT_TABLES:
    Base.metadata.tables[name].to_metadata(agent_metadata)

__all__ = [
    "AgentRun",
    "AgentRunStatus",
    "Base",
    "Conversation",
    "ConversationStatus",
    "ConversationSummary",
    "CustomerMemory",
    "MemoryType",
    "Message",
    "MessageRole",
    "ToolExecution",
    "ToolExecutionStatus",
    "agent_metadata",
]

"""Shared state for one Agent graph run.

Only ``messages`` has a reducer. The other fields use LangGraph's default
overwrite behavior, so nodes must return an absolute tool-call count.
"""

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ErrorInfo(TypedDict):
    """A safe, structured error for graph routing and diagnostics."""

    code: str
    source: Literal["llm", "guard", "tool"]
    tool_call_id: str | None
    detail: str


class GuardResult(TypedDict):
    """Decision for exactly one pending tool call."""

    tool_call_id: str
    decision: Literal["allowed", "rejected", "needs_confirmation"]
    reason: str | None


class AgentState(TypedDict):
    """Checkpointed working state for one AgentRun.

    ``tool_call_count`` counts dispatched logical tool calls, not retries.
    Only the serial tool-dispatch node should update it. ``last_error`` and
    ``guard_result`` must be cleared explicitly when no longer applicable.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    tool_call_count: int
    last_error: ErrorInfo | None
    guard_result: GuardResult | None

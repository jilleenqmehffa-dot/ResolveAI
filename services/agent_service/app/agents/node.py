"""Nodes for the read-only Commerce Agent graph."""

import json
from asyncio import sleep
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from pydantic import BaseModel, ValidationError

from services.agent_service.app.agents.routing import route_after_guard
from services.agent_service.app.agents.state import AgentState, ErrorInfo, GuardResult
from services.agent_service.app.execution.manager import (
    ExecutionManager,
    RecoveryAction,
)
from services.agent_service.app.execution.tool_invocations import ToolInvocationStore
from services.agent_service.app.models.agent import ToolExecutionStatus
from services.agent_service.app.transport.commer_client import CommerceClientError

ContextLoader = Callable[[AgentState], Awaitable[Sequence[BaseMessage]]]

DEFAULT_SYSTEM_PROMPT = (
    "You are a retail assistant. Use the available read tools to verify product "
    "and order facts, including current after-sales case status. Treat tool "
    "results as the source of truth. A closed case does not by itself prove "
    "that a refund was paid. "
    "Only call one tool at a time. Do not claim an order was changed."
)


class AgentNodes:
    """Bind graph nodes to one model, one user-scoped tool set, and context loader."""

    def __init__(
        self,
        model_with_tools: Runnable[Any, Any],
        tools: Sequence[BaseTool],
        *,
        context_loader: ContextLoader | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_tool_calls: int = 8,
    ) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be positive")
        if not system_prompt.strip():
            raise ValueError("system_prompt must not be empty")
        self._model = model_with_tools
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("tool names must be unique")
        self._context_loader = context_loader
        self._system_prompt = system_prompt
        self._max_tool_calls = max_tool_calls

    async def prepare_context(self, state: AgentState) -> dict[str, Any]:
        """Put the system prompt, prior session messages, and new user turn in order."""
        incoming = state["messages"]
        if not incoming or not isinstance(incoming[-1], HumanMessage):
            raise ValueError("prepare_context requires a latest user message")
        history = await self._context_loader(state) if self._context_loader else []
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                SystemMessage(content=self._system_prompt),
                *history,
                *incoming,
            ],
            "last_error": None,
            "guard_result": None,
        }

    async def agent(self, state: AgentState) -> dict[str, Any]:
        """Ask DeepSeek for either one tool call or a final answer."""
        response = await self._model.ainvoke(state["messages"])
        if not isinstance(response, AIMessage):
            raise TypeError("agent model must return an AIMessage")
        if response.invalid_tool_calls:
            raise ValueError("agent returned malformed tool calls")
        if not response.tool_calls and not response.content:
            raise ValueError("agent returned an empty reply")
        return {"messages": [response], "guard_result": None}

    def tool_guard(self, state: AgentState) -> dict[str, Any]:
        """Allow one known call with valid args and remaining tool budget."""
        if not state["messages"] or not isinstance(state["messages"][-1], AIMessage):
            raise ValueError("tool_guard requires the latest AIMessage")
        calls = state["messages"][-1].tool_calls
        if not calls:
            raise ValueError("tool_guard requires a tool call")
        if any(not call.get("id") for call in calls):
            raise ValueError("tool call has no ID")

        reason: str | None = None
        if len(calls) != 1:
            reason = "Call exactly one tool at a time"
        elif state["tool_call_count"] >= self._max_tool_calls:
            reason = "Tool call limit reached"
        else:
            call = calls[0]
            tool = self._tools.get(call["name"])
            if tool is None:
                reason = f"Unknown tool: {call['name']}"
            elif not isinstance(tool.args_schema, type) or not issubclass(
                tool.args_schema, BaseModel
            ):
                reason = f"Tool has no input schema: {call['name']}"
            else:
                try:
                    tool.args_schema.model_validate(call["args"])
                except ValidationError:
                    reason = f"Invalid arguments for {call['name']}"

        result: GuardResult = {
            "tool_call_id": calls[0]["id"],
            "decision": "rejected" if reason else "allowed",
            "reason": reason,
        }
        error: ErrorInfo | None = (
            {
                "code": "guard_rejected",
                "source": "guard",
                "tool_call_id": calls[0]["id"],
                "detail": reason,
            }
            if reason
            else None
        )
        return {"guard_result": result, "last_error": error}

    async def tools(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Dispatch one approved call, retry transient reads, and record attempts."""
        if route_after_guard(state) != "approved":
            raise ValueError("tools node requires an approved call")
        call = state["messages"][-1].tool_calls[0]
        tool = self._tools[call["name"]]
        configurable = config.get("configurable") or {}
        manager = configurable.get("execution_manager") or ExecutionManager()
        invocations = configurable.get("tool_invocations")
        if not isinstance(manager, ExecutionManager):
            raise TypeError("execution_manager must be an ExecutionManager")
        if invocations is not None and not isinstance(invocations, ToolInvocationStore):
            raise TypeError("tool_invocations must be a ToolInvocationStore")

        started_at = monotonic()
        local_attempt_count = 0
        error: ErrorInfo | None = None
        while True:
            if invocations is not None:
                record = await invocations.start(
                    call_id=call["id"],
                    tool_name=call["name"],
                    args=call["args"],
                    max_attempts=manager.policy.max_attempts,
                )
                if not record.should_invoke:
                    if record.status is ToolExecutionStatus.SUCCEEDED:
                        content = json.dumps(
                            {
                                "ok": True,
                                "data": (record.result_payload or {}).get("data"),
                            },
                            ensure_ascii=False,
                            default=str,
                        )
                    else:
                        code = record.error_code or "invocation_not_replayable"
                        detail = (
                            (record.result_payload or {})
                            .get("error", {})
                            .get("detail", "Tool invocation cannot be replayed")
                        )
                        error = {
                            "code": code,
                            "source": "tool",
                            "tool_call_id": call["id"],
                            "detail": detail,
                        }
                        content = json.dumps(
                            {"ok": False, "error": {"code": code, "detail": detail}},
                            ensure_ascii=False,
                        )
                    break
                attempt_count = record.attempt_count
            else:
                local_attempt_count += 1
                attempt_count = local_attempt_count

            try:
                data = await tool.ainvoke(call["args"])
            except CommerceClientError as exc:
                decision = manager.decide_failure(
                    exc,
                    is_write=False,  # Current Commerce tools are all reads.
                    attempt_count=attempt_count,
                    elapsed_seconds=monotonic() - started_at,
                )
                error = {
                    "code": exc.code,
                    "source": "tool",
                    "tool_call_id": call["id"],
                    "detail": str(exc),
                }
                payload = {"error": {"code": exc.code, "detail": str(exc)}}
                if invocations is not None:
                    if decision.action is RecoveryAction.RETRY:
                        status = ToolExecutionStatus.RETRY_PENDING
                    elif decision.action in (
                        RecoveryAction.VERIFY_RESULT,
                        RecoveryAction.NEEDS_REVIEW,
                    ):
                        status = ToolExecutionStatus.UNKNOWN
                    else:
                        status = ToolExecutionStatus.FAILED
                    await invocations.finish(
                        call_id=call["id"],
                        attempt_count=attempt_count,
                        status=status,
                        result_payload=payload,
                        error_code=exc.code,
                    )
                if decision.action is RecoveryAction.RETRY:
                    await sleep(decision.retry_after_seconds or 0)
                    continue
                content = json.dumps({"ok": False, **payload}, ensure_ascii=False)
                break
            else:
                if invocations is not None:
                    await invocations.finish(
                        call_id=call["id"],
                        attempt_count=attempt_count,
                        status=ToolExecutionStatus.SUCCEEDED,
                        result_payload={"data": data},
                    )
                content = json.dumps(
                    {"ok": True, "data": data}, ensure_ascii=False, default=str
                )
                error = None
                break
        return {
            "messages": [ToolMessage(content=content, tool_call_id=call["id"])],
            "tool_call_count": state["tool_call_count"] + 1,
            "guard_result": None,
            "last_error": error,
        }

    def rejected(self, state: AgentState) -> dict[str, Any]:
        """Return one rejection result per call so the model can continue safely."""
        result = state["guard_result"]
        if result is None or result["decision"] != "rejected":
            raise ValueError("rejected node requires a guard rejection")
        response = state["messages"][-1]
        if not isinstance(response, AIMessage) or not response.tool_calls:
            raise ValueError("rejected node requires tool calls")
        content = json.dumps(
            {
                "ok": False,
                "error": {"code": "guard_rejected", "detail": result["reason"]},
            },
            ensure_ascii=False,
        )
        return {
            "messages": [
                ToolMessage(content=content, tool_call_id=call["id"])
                for call in response.tool_calls
            ],
            "guard_result": None,
        }

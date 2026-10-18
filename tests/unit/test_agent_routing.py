"""Routing never sends an unchecked or mismatched tool call to tools."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from services.agent_service.app.agents.routing import (
    route_after_agent,
    route_after_guard,
)
from services.agent_service.app.agents.state import AgentState


def state_with(message: AIMessage | HumanMessage) -> AgentState:
    return {
        "messages": [message],
        "tool_call_count": 0,
        "last_error": None,
        "guard_result": None,
    }


def tool_message(call_id: str = "call-1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "get_order", "args": {"order_id": "o1"}}],
    )


def test_agent_routes_reply_or_tool_call() -> None:
    assert route_after_agent(state_with(AIMessage(content="Done"))) == "end"
    assert route_after_agent(state_with(tool_message())) == "tool_guard"


def test_agent_rejects_missing_or_non_assistant_message() -> None:
    with pytest.raises(ValueError, match="did not return"):
        route_after_agent(state_with(AIMessage(content="")) | {"messages": []})
    with pytest.raises(TypeError, match="AIMessage"):
        route_after_agent(state_with(HumanMessage(content="Hello")))


@pytest.mark.parametrize("decision", ["allowed", "rejected"])
def test_guard_routes_each_decision(decision: str) -> None:
    state = state_with(tool_message())
    state["guard_result"] = {
        "tool_call_id": "call-1",
        "decision": decision,
        "reason": None,
    }
    assert route_after_guard(state) == (
        "approved" if decision == "allowed" else decision
    )


def test_guard_rejects_unsupported_decision() -> None:
    state = state_with(tool_message())
    state["guard_result"] = {
        "tool_call_id": "call-1",
        "decision": "needs_confirmation",  # type: ignore[typeddict-item]
        "reason": None,
    }
    with pytest.raises(ValueError, match="unknown guard decision"):
        route_after_guard(state)


def test_guard_fails_closed_on_missing_or_mismatched_approval() -> None:
    state = state_with(tool_message())
    with pytest.raises(ValueError, match="guard_result"):
        route_after_guard(state)

    state["guard_result"] = {
        "tool_call_id": "different-call",
        "decision": "allowed",
        "reason": None,
    }
    with pytest.raises(ValueError, match="does not match"):
        route_after_guard(state)

    state["messages"] = [tool_message("call-1"), tool_message("call-2")]
    state["guard_result"]["tool_call_id"] = "call-2"
    assert route_after_guard(state) == "approved"

    state["messages"] = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "call-1", "name": "get_order", "args": {}},
                {"id": "call-2", "name": "get_order", "args": {}},
            ],
        )
    ]
    with pytest.raises(ValueError, match="does not match"):
        route_after_guard(state)

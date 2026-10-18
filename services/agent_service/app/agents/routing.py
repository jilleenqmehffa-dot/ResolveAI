"""Pure routing decisions for the Agent graph's conditional edges."""

from typing import Literal

from langchain_core.messages import AIMessage

from services.agent_service.app.agents.state import AgentState

AgentRoute = Literal["tool_guard", "end"]
GuardRoute = Literal["approved", "rejected"]


def route_after_agent(state: AgentState) -> AgentRoute:
    """Route an assistant tool call to guard, or a final reply to END."""
    if not state["messages"]:
        raise ValueError("agent did not return a message")

    response = state["messages"][-1]
    if not isinstance(response, AIMessage):
        raise TypeError("agent's last message must be an AIMessage")

    return "tool_guard" if response.tool_calls else "end"


def route_after_guard(state: AgentState) -> GuardRoute:
    """Route one checked call according to the guard's decision."""
    result = state["guard_result"]
    if result is None:
        raise ValueError("tool_guard did not set guard_result")

    decision = result["decision"]
    if decision == "allowed":
        if not state["messages"]:
            raise ValueError("approved tool call has no assistant message")
        response = state["messages"][-1]
        if (
            not isinstance(response, AIMessage)
            or len(response.tool_calls) != 1
            or response.tool_calls[0]["id"] != result["tool_call_id"]
        ):
            raise ValueError("approved tool call does not match the latest message")
        return "approved"

    if decision == "rejected":
        return "rejected"

    raise ValueError(f"unknown guard decision: {decision}")

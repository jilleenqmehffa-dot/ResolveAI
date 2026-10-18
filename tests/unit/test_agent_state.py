"""Verify the Agent graph's message reducer and overwrite fields."""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from services.agent_service.app.agents.state import AgentState


def test_agent_state_merge_and_overwrite() -> None:
    graph = StateGraph(AgentState)

    def first(_state: AgentState) -> dict:
        return {
            "messages": [AIMessage(content="draft", id="answer")],
            "tool_call_count": 1,
            "last_error": {
                "code": "TEMPORARY_FAILURE",
                "source": "tool",
                "tool_call_id": "call-1",
                "detail": "Tool unavailable",
            },
            "guard_result": {
                "tool_call_id": "call-1",
                "decision": "allowed",
                "reason": None,
            },
        }

    def second(_state: AgentState) -> dict:
        return {
            "messages": [AIMessage(content="final", id="answer")],
            "tool_call_count": 2,
            "last_error": None,
            "guard_result": None,
        }

    graph.add_node("first", first)
    graph.add_node("second", second)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)

    result = graph.compile().invoke(
        {
            "messages": [HumanMessage(content="Hello", id="user-1")],
            "tool_call_count": 0,
            "last_error": None,
            "guard_result": None,
        }
    )

    assert [message.content for message in result["messages"]] == ["Hello", "final"]
    assert result["tool_call_count"] == 2
    assert result["last_error"] is None
    assert result["guard_result"] is None

"""Wire the Agent nodes and Commerce tools."""

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from services.agent_service.app.agents.llm import create_deepseek_model
from services.agent_service.app.agents.node import (
    DEFAULT_SYSTEM_PROMPT,
    AgentNodes,
    ContextLoader,
)
from services.agent_service.app.agents.routing import (
    route_after_agent,
    route_after_guard,
)
from services.agent_service.app.agents.state import AgentState
from services.agent_service.app.agents.tools import create_commerce_tools
from services.agent_service.app.transport.commer_client import CommerceClient


def create_agent_graph(
    client: CommerceClient,
    *,
    model: BaseChatModel | None = None,
    context_loader: ContextLoader | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_tool_calls: int = 8,
    recursion_limit: int = 40,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile the diagram's flow; the caller owns the CommerceClient lifecycle."""
    if recursion_limit < 5:
        raise ValueError("recursion_limit must be at least 5")
    tools = create_commerce_tools(client)
    chat_model = model if model is not None else create_deepseek_model()
    nodes = AgentNodes(
        chat_model.bind_tools(tools),
        tools,
        context_loader=context_loader,
        system_prompt=system_prompt,
        max_tool_calls=max_tool_calls,
    )

    graph = StateGraph(AgentState)
    graph.add_node("prepare_context", nodes.prepare_context)
    graph.add_node("agent", nodes.agent)
    graph.add_node("tool_guard", nodes.tool_guard)
    graph.add_node("tools", nodes.tools)
    graph.add_node("rejected", nodes.rejected)
    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "agent")
    graph.add_conditional_edges(
        "agent", route_after_agent, {"tool_guard": "tool_guard", "end": END}
    )
    graph.add_conditional_edges(
        "tool_guard", route_after_guard, {"approved": "tools", "rejected": "rejected"}
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("rejected", "agent")
    return graph.compile(checkpointer=checkpointer).with_config(
        {"recursion_limit": recursion_limit}
    )

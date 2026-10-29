"""The graph follows the requested guard, tool, rejection, and return paths."""

import json
from collections.abc import Sequence

import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from services.agent_service.app.agents.graph import create_agent_graph
from services.agent_service.app.agents.state import AgentState
from services.agent_service.app.transport.commer_client import CommerceClient


class ScriptedModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses
        self.seen: list[list[BaseMessage]] = []
        self.bound_tool_names: list[str] = []

    def bind_tools(self, tools) -> "ScriptedModel":
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> AIMessage:
        self.seen.append(list(messages))
        return self.responses.pop(0)


def initial_state() -> AgentState:
    return {
        "messages": [HumanMessage(content="Show me the product")],
        "tool_call_count": 0,
        "last_error": None,
        "guard_result": None,
    }


def call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "name": name, "args": args}


@pytest.mark.asyncio
async def test_agent_final_answer_ends_without_tool_request() -> None:
    model = ScriptedModel([AIMessage(content="Hello")])
    async with CommerceClient("http://commerce.test", "u1") as client:
        result = await create_agent_graph(client, model=model).ainvoke(initial_state())

    assert result["messages"][-1].content == "Hello"
    assert result["tool_call_count"] == 0
    assert not any(isinstance(item, ToolMessage) for item in result["messages"])


@pytest.mark.asyncio
async def test_graph_prepares_context_runs_tool_then_returns_to_agent() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={"product_id": "p1", "variants": []})

    async def load_history(_state: AgentState) -> list[BaseMessage]:
        return [
            HumanMessage(content="Earlier question"),
            AIMessage(content="Earlier answer"),
        ]

    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[call("c1", "get_product", {"product_id": " p1 "})],
            ),
            AIMessage(content="Product p1 is available"),
        ]
    )
    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        graph = create_agent_graph(client, model=model, context_loader=load_history)
        result = await graph.ainvoke(initial_state())

    assert model.bound_tool_names == [
        "list_products",
        "get_product",
        "get_item",
        "list_orders",
        "get_order",
        "list_after_sales_cases",
        "get_after_sales_case",
        "mark_after_sales_refunded",
    ]
    assert [type(item).__name__ for item in model.seen[0][:4]] == [
        "SystemMessage",
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
    ]
    assert requests == ["/products/p1"]
    tool_result = next(
        item for item in result["messages"] if isinstance(item, ToolMessage)
    )
    assert tool_result.tool_call_id == "c1"
    assert json.loads(tool_result.content) == {
        "ok": True,
        "data": {"product_id": "p1", "variants": []},
    }
    assert result["tool_call_count"] == 1
    assert result["messages"][-1].content == "Product p1 is available"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("get_order", {"order_id": "o1", "user_id": "other"}),
        ("get_order", {"order_id": " "}),
        ("unknown_tool", {}),
    ],
)
async def test_guard_rejects_bad_call_without_http(name: str, args: dict) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    model = ScriptedModel(
        [
            AIMessage(content="", tool_calls=[call("c1", name, args)]),
            AIMessage(content="Cannot do that"),
        ]
    )
    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        result = await create_agent_graph(client, model=model).ainvoke(initial_state())

    assert requests == []
    assert result["tool_call_count"] == 0
    tool_result = next(
        item for item in result["messages"] if isinstance(item, ToolMessage)
    )
    assert tool_result.tool_call_id == "c1"
    assert json.loads(tool_result.content)["error"]["code"] == "guard_rejected"


@pytest.mark.asyncio
async def test_guard_rejects_all_calls_in_one_message() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    call("c1", "list_products", {}),
                    call("c2", "list_orders", {}),
                ],
            ),
            AIMessage(content="I will try one at a time"),
        ]
    )
    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        result = await create_agent_graph(client, model=model).ainvoke(initial_state())

    assert requests == []
    assert result["tool_call_count"] == 0
    assert [
        item.tool_call_id
        for item in result["messages"]
        if isinstance(item, ToolMessage)
    ] == ["c1", "c2"]


@pytest.mark.asyncio
async def test_guard_enforces_tool_call_limit() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json=[])

    model = ScriptedModel(
        [
            AIMessage(content="", tool_calls=[call("c1", "list_products", {})]),
            AIMessage(content="", tool_calls=[call("c2", "list_products", {})]),
            AIMessage(content="Done"),
        ]
    )
    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        result = await create_agent_graph(
            client, model=model, max_tool_calls=1
        ).ainvoke(initial_state())

    assert requests == ["/products"]
    assert result["tool_call_count"] == 1
    messages = [item for item in result["messages"] if isinstance(item, ToolMessage)]
    assert (
        json.loads(messages[1].content)["error"]["detail"] == "Tool call limit reached"
    )


@pytest.mark.asyncio
async def test_tool_http_error_returns_to_agent() -> None:
    model = ScriptedModel(
        [
            AIMessage(
                content="", tool_calls=[call("c1", "get_order", {"order_id": "o1"})]
            ),
            AIMessage(content="I could not find that order"),
        ]
    )
    async with CommerceClient(
        "http://commerce.test",
        "u1",
        transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
    ) as client:
        result = await create_agent_graph(client, model=model).ainvoke(initial_state())

    tool_result = next(
        item for item in result["messages"] if isinstance(item, ToolMessage)
    )
    assert json.loads(tool_result.content)["error"]["code"] == "not_found"
    assert result["tool_call_count"] == 1
    assert result["last_error"]["code"] == "not_found"

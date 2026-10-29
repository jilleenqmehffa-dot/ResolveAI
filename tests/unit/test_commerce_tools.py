"""Agent tools use the current customer's Commerce context."""

import httpx
import pytest
from pydantic import ValidationError

from services.agent_service.app.agents.tools import create_commerce_tools
from services.agent_service.app.agents.tools.schemas import (
    EmptyToolArgs,
    GetAfterSalesCaseArgs,
    GetItemArgs,
    GetOrderArgs,
    GetProductArgs,
)
from services.agent_service.app.transport.commer_client import (
    CommerceClient,
    CommerceClientError,
)


@pytest.mark.asyncio
async def test_tools_call_commerce_and_keep_user_id_out_of_tool_arguments() -> None:
    requests: list[tuple[str, str | None]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(
            (
                request.url.path,
                request.url.params.get("user_id")
                or request.url.params.get("customer_id"),
            )
        )
        if request.url.path in ("/products", "/customers/u1/orders", "/after-sales/"):
            return httpx.Response(200, json=[{"id": "one"}])
        if request.method == "PUT":
            assert request.headers["x-agent-token"] == "test-agent-token"
            assert request.headers["x-customer-id"] == "u1"
            assert request.content == b""
            return httpx.Response(
                200, json={"status": "CLOSED", "resolution": "REFUNDED"}
            )
        return httpx.Response(200, json={"id": "one"})

    async with CommerceClient(
        "http://commerce.test",
        "u1",
        agent_token="test-agent-token",
        transport=httpx.MockTransport(respond),
    ) as client:
        tools = {item.name: item for item in create_commerce_tools(client)}
        assert set(tools) == {
            "list_products",
            "get_product",
            "get_item",
            "list_orders",
            "get_order",
            "list_after_sales_cases",
            "get_after_sales_case",
            "mark_after_sales_refunded",
        }
        assert tools["list_orders"].args == {}
        assert set(tools["get_order"].args) == {"order_id"}
        assert {name: item.args_schema for name, item in tools.items()} == {
            "list_products": EmptyToolArgs,
            "get_product": GetProductArgs,
            "get_item": GetItemArgs,
            "list_orders": EmptyToolArgs,
            "get_order": GetOrderArgs,
            "list_after_sales_cases": EmptyToolArgs,
            "get_after_sales_case": GetAfterSalesCaseArgs,
            "mark_after_sales_refunded": GetAfterSalesCaseArgs,
        }

        assert await tools["list_products"].ainvoke({}) == [{"id": "one"}]
        assert await tools["get_product"].ainvoke({"product_id": "p1"}) == {"id": "one"}
        assert await tools["get_item"].ainvoke({"item_id": "sku1"}) == {"id": "one"}
        assert await tools["list_orders"].ainvoke({}) == [{"id": "one"}]
        assert await tools["get_order"].ainvoke({"order_id": "o1"}) == {"id": "one"}
        assert await tools["list_after_sales_cases"].ainvoke({}) == [{"id": "one"}]
        assert await tools["get_after_sales_case"].ainvoke({"case_id": "c1"}) == {
            "id": "one"
        }
        assert await tools["mark_after_sales_refunded"].ainvoke({"case_id": "c1"}) == {
            "status": "CLOSED",
            "resolution": "REFUNDED",
        }

    assert requests == [
        ("/products", None),
        ("/products/p1", None),
        ("/items/sku1", None),
        ("/customers/u1/orders", None),
        ("/orders/o1", "u1"),
        ("/after-sales/", "u1"),
        ("/after-sales/c1", "u1"),
        ("/after-sales/c1/resolution/refunded", None),
    ]


@pytest.mark.asyncio
async def test_tool_preserves_classified_client_error() -> None:
    async with CommerceClient(
        "http://commerce.test",
        "u1",
        transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
    ) as client:
        tools = {item.name: item for item in create_commerce_tools(client)}
        with pytest.raises(CommerceClientError) as error:
            await tools["get_order"].ainvoke({"order_id": "missing"})
    assert error.value.code == "not_found"


@pytest.mark.asyncio
async def test_tool_rejects_model_supplied_user_id_before_http_request() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        tools = {item.name: item for item in create_commerce_tools(client)}
        with pytest.raises(ValidationError):
            await tools["get_order"].ainvoke(
                {"order_id": "o1", "user_id": "another-user"}
            )
    assert requests == []

"""Read-only LangChain tools backed by one user-scoped Commerce client."""

from typing import Any

from langchain_core.tools import BaseTool, tool

from services.agent_service.app.agents.tools.schemas import (
    EmptyToolArgs,
    GetItemArgs,
    GetOrderArgs,
    GetProductArgs,
)
from services.agent_service.app.transport.commer_client import CommerceClient


def create_commerce_tools(client: CommerceClient) -> list[BaseTool]:
    """Bind Commerce read tools to a client created for the current user.

    The caller owns the client's lifecycle. CommerceClientError is propagated so
    the graph's tool executor can apply its retry and failure policy.
    """

    @tool(args_schema=EmptyToolArgs)
    async def list_products() -> list[dict[str, Any]]:
        """List available product names and IDs when the product ID is unknown."""
        return await client.list_products()

    @tool(args_schema=GetProductArgs)
    async def get_product(product_id: str) -> dict[str, Any]:
        """Get a product by ID, including its variants, prices, and availability."""
        return await client.get_product(product_id)

    @tool(args_schema=GetItemArgs)
    async def get_item(item_id: str) -> dict[str, Any]:
        """Get one item by ID to check its options, current price, and availability."""
        return await client.get_item(item_id)

    @tool(args_schema=EmptyToolArgs)
    async def list_orders() -> list[dict[str, Any]]:
        """List the current user's order IDs and statuses."""
        return await client.list_orders()

    @tool(args_schema=GetOrderArgs)
    async def get_order(order_id: str) -> dict[str, Any]:
        """Get details for one order owned by the current user."""
        return await client.get_order(order_id)

    return [list_products, get_product, get_item, list_orders, get_order]

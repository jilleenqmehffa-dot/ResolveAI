"""LangChain tools backed by one user-scoped Commerce client."""

from typing import Any

from langchain_core.tools import BaseTool, tool

from services.agent_service.app.agents.tools.schemas import (
    AddressArgs,
    CancelOrderArgs,
    ChangeOrderPaymentArgs,
    EmptyToolArgs,
    GetAfterSalesCaseArgs,
    GetItemArgs,
    GetOrderArgs,
    GetProductArgs,
    OrderAddressArgs,
    ReplaceOrderItemsArgs,
    ReturnOrderItemsArgs,
)
from services.agent_service.app.transport.commer_client import CommerceClient


def create_commerce_tools(client: CommerceClient) -> list[BaseTool]:
    """Bind Commerce tools to a client created for the current user.

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
        """List the user's orders in source order, with later orders at higher positions."""
        return await client.list_orders()

    @tool(args_schema=GetOrderArgs)
    async def get_order(order_id: str) -> dict[str, Any]:
        """Get details for one order owned by the current user."""
        return await client.get_order(order_id)

    @tool(args_schema=EmptyToolArgs)
    async def get_customer() -> dict[str, Any]:
        """Get the customer's profile, ordered order IDs, and payment methods."""
        return await client.get_customer()

    @tool(args_schema=ReplaceOrderItemsArgs)
    async def modify_pending_order_items(
        order_id: str,
        item_ids: list[str],
        new_item_ids: list[str],
        payment_method_id: str,
    ) -> dict[str, Any]:
        """Replace items on a pending order with available variants of the same products."""
        return await client.modify_pending_order_items(
            order_id, item_ids, new_item_ids, payment_method_id
        )

    @tool(args_schema=ReplaceOrderItemsArgs)
    async def exchange_delivered_order_items(
        order_id: str,
        item_ids: list[str],
        new_item_ids: list[str],
        payment_method_id: str,
    ) -> dict[str, Any]:
        """Request an exchange of delivered order items."""
        return await client.exchange_delivered_order_items(
            order_id, item_ids, new_item_ids, payment_method_id
        )

    @tool(args_schema=ReturnOrderItemsArgs)
    async def return_delivered_order_items(
        order_id: str,
        item_ids: list[str],
        payment_method_id: str,
    ) -> dict[str, Any]:
        """Request a return of delivered order items."""
        return await client.return_delivered_order_items(
            order_id, item_ids, payment_method_id
        )

    @tool(args_schema=CancelOrderArgs)
    async def cancel_pending_order(order_id: str, reason: str) -> dict[str, Any]:
        """Cancel a pending order for the supplied reason."""
        return await client.cancel_pending_order(order_id, reason)

    @tool(args_schema=OrderAddressArgs)
    async def modify_pending_order_address(
        order_id: str,
        address1: str,
        address2: str,
        city: str,
        state: str,
        country: str,
        zip: str,
    ) -> dict[str, Any]:
        """Change the shipping address of a pending order."""
        return await client.modify_pending_order_address(
            order_id,
            {
                "address1": address1,
                "address2": address2,
                "city": city,
                "state": state,
                "country": country,
                "zip": zip,
            },
        )

    @tool(args_schema=ChangeOrderPaymentArgs)
    async def modify_pending_order_payment(
        order_id: str,
        payment_method_id: str,
    ) -> dict[str, Any]:
        """Change a pending order's payment method to one owned by the customer."""
        return await client.modify_pending_order_payment(order_id, payment_method_id)

    @tool(args_schema=AddressArgs)
    async def modify_user_address(
        address1: str,
        address2: str,
        city: str,
        state: str,
        country: str,
        zip: str,
    ) -> dict[str, Any]:
        """Change the current customer's default address."""
        return await client.modify_user_address(
            {
                "address1": address1,
                "address2": address2,
                "city": city,
                "state": state,
                "country": country,
                "zip": zip,
            }
        )

    @tool(args_schema=EmptyToolArgs)
    async def list_after_sales_cases() -> list[dict[str, Any]]:
        """List the current user's after-sales cases and current processing status."""
        return await client.list_after_sales_cases()

    @tool(args_schema=GetAfterSalesCaseArgs)
    async def get_after_sales_case(case_id: str) -> dict[str, Any]:
        """Get one after-sales case's type, status, and final resolution by case ID."""
        return await client.get_after_sales_case(case_id)

    @tool(args_schema=GetAfterSalesCaseArgs)
    async def mark_after_sales_refunded(case_id: str) -> dict[str, Any]:
        """Close a refund-type after-sales case as REFUNDED; does not issue or prove payment."""
        return await client.mark_after_sales_refunded(case_id)

    return [
        list_products,
        get_product,
        get_item,
        list_orders,
        get_order,
        get_customer,
        modify_pending_order_items,
        exchange_delivered_order_items,
        return_delivered_order_items,
        cancel_pending_order,
        modify_pending_order_address,
        modify_pending_order_payment,
        modify_user_address,
        list_after_sales_cases,
        get_after_sales_case,
        mark_after_sales_refunded,
    ]

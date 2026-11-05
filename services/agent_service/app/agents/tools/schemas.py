"""Input contracts presented to the model and enforced by the tool guard."""

from pydantic import BaseModel, ConfigDict, Field


class ToolArgs(BaseModel):
    """Reject undeclared arguments, including model-supplied user IDs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class EmptyToolArgs(ToolArgs):
    """Input for read tools that require no model-generated arguments."""


class GetProductArgs(ToolArgs):
    """Identify the product whose variants should be listed."""

    product_id: str = Field(min_length=1, description="Product ID from list_products")


class GetItemArgs(ToolArgs):
    """Identify one product variant."""

    item_id: str = Field(min_length=1, description="Item ID from get_product")


class GetOrderArgs(ToolArgs):
    """Identify one order owned by the current user."""

    order_id: str = Field(min_length=1, description="Order ID from list_orders")


class GetAfterSalesCaseArgs(ToolArgs):
    """Identify an after-sales case owned by the current user."""

    case_id: str = Field(
        min_length=1, description="Case ID from list_after_sales_cases"
    )


class ReplaceOrderItemsArgs(GetOrderArgs):
    item_ids: list[str] = Field(min_length=1)
    new_item_ids: list[str] = Field(min_length=1)
    payment_method_id: str = Field(min_length=1)


class ReturnOrderItemsArgs(GetOrderArgs):
    item_ids: list[str] = Field(min_length=1)
    payment_method_id: str = Field(min_length=1)


class CancelOrderArgs(GetOrderArgs):
    reason: str = Field(min_length=1)


class AddressArgs(ToolArgs):
    address1: str
    address2: str
    city: str
    state: str
    country: str
    zip: str


class OrderAddressArgs(GetOrderArgs, AddressArgs):
    pass


class ChangeOrderPaymentArgs(GetOrderArgs):
    payment_method_id: str = Field(min_length=1)

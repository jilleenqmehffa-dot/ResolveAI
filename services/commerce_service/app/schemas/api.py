"""HTTP request and response models for the retail API."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductTypeResponse(ApiModel):
    product_id: str
    name: str


class ItemResponse(ApiModel):
    item_id: str
    product_id: str
    product_name: str
    options: dict[str, str]
    price: Decimal
    available: bool


class ProductResponse(ApiModel):
    product_id: str
    name: str
    variants: list[ItemResponse]


class AddressResponse(ApiModel):
    address1: str | None
    address2: str | None
    city: str | None
    state: str | None
    country: str | None
    zip: str | None


class CustomerResponse(ApiModel):
    user_id: str
    first_name: str
    last_name: str
    email: str
    address: AddressResponse


class PaymentMethodResponse(ApiModel):
    payment_method_id: str
    source: str
    brand: str | None
    last_four: str | None
    balance: Decimal | None


class OrderSummaryResponse(ApiModel):
    order_id: str
    user_id: str
    status: str


class OrderItemResponse(ApiModel):
    position: int
    name: str
    product_id: str
    item_id: str
    price: Decimal
    options: dict[str, str]


class OrderPaymentResponse(ApiModel):
    position: int
    transaction_type: str
    amount: Decimal
    payment_method_id: str


class ExchangeItemResponse(ApiModel):
    position: int
    old_item_id: str
    new_item_id: str


class ReturnItemResponse(ApiModel):
    position: int
    item_id: str


class OrderResponse(OrderSummaryResponse):
    address: AddressResponse
    cancel_reason: str | None
    exchange_payment_method_id: str | None
    exchange_price_difference: Decimal | None
    return_payment_method_id: str | None
    return_estimated_refund: Decimal | None
    items: list[OrderItemResponse]
    payment_history: list[OrderPaymentResponse]
    exchange_items: list[ExchangeItemResponse]
    return_items: list[ReturnItemResponse]


class OrderActionRequest(ApiModel):
    user_id: str
    item_ids: list[str] = Field(min_length=1)
    payment_method_id: str


class ReplaceItemsRequest(OrderActionRequest):
    new_item_ids: list[str] = Field(min_length=1)

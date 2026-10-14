"""Structured, transport-independent retail read results."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ProductType:
    product_id: str
    name: str


@dataclass(frozen=True)
class ItemDetails:
    item_id: str
    product_id: str
    product_name: str
    options: dict[str, str]
    price: Decimal
    available: bool


@dataclass(frozen=True)
class ProductDetails:
    product_id: str
    name: str
    variants: tuple[ItemDetails, ...]

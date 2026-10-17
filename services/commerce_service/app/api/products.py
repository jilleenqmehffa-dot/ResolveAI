"""Retail product and item lookup routes."""

from fastapi import APIRouter

from services.commerce_service.app.api.dependencies import RetailServiceDep
from services.commerce_service.app.schemas.api import (
    ItemResponse,
    ProductResponse,
    ProductTypeResponse,
)

router = APIRouter(tags=["products"])


@router.get("/products", response_model=list[ProductTypeResponse])
def list_products(service: RetailServiceDep) -> list[ProductTypeResponse]:
    return [
        ProductTypeResponse(product_id=product.product_id, name=product.name)
        for product in service.list_all_product_types()
    ]


@router.get("/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: str, service: RetailServiceDep) -> ProductResponse:
    product = service.get_product_details(product_id)
    return ProductResponse(
        product_id=product.product_id,
        name=product.name,
        variants=[ItemResponse.model_validate(vars(item)) for item in product.variants],
    )


@router.get("/items/{item_id}", response_model=ItemResponse)
def get_item(item_id: str, service: RetailServiceDep) -> ItemResponse:
    return ItemResponse.model_validate(vars(service.get_item_details(item_id)))

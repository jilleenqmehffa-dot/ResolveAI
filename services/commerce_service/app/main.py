"""FastAPI application entry point."""

from fastapi import FastAPI

from services.commerce_service.app.api.after_sales import router as after_sales_router
from services.commerce_service.app.api.customers import router as customers_router
from services.commerce_service.app.api.errors import register_retail_error_handlers
from services.commerce_service.app.api.health import router as health_router
from services.commerce_service.app.api.orders import router as orders_router
from services.commerce_service.app.api.products import router as products_router


def create_app() -> FastAPI:
    app = FastAPI(title="ResolveAI", version="0.1.0")
    register_retail_error_handlers(app)
    app.include_router(health_router)
    app.include_router(products_router)
    app.include_router(customers_router)
    app.include_router(orders_router)
    app.include_router(after_sales_router)
    return app


app = create_app()

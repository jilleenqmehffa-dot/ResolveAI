"""Read-only Commerce HTTP adapter for Agent tools.

Create one client per authenticated user context. The user ID must come from
the Agent execution context, not from an LLM-generated tool argument.
"""

from typing import Any, Self, cast
from urllib.parse import quote

import httpx


class CommerceClientError(Exception):
    """A classified Commerce request failure safe to handle at the tool boundary."""

    def __init__(
        self, code: str, detail: str, *, retryable: bool, status_code: int | None = None
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class CommerceClient:
    """Expose the five supported read operations through Commerce's public API."""

    def __init__(
        self,
        base_url: str,
        user_id: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        if not user_id.strip():
            raise ValueError("user_id must not be empty")
        self.user_id = user_id
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _path_id(value: str) -> str:
        return quote(value, safe="")

    async def _get(
        self, path: str, *, params: dict[str, str] | None = None, is_list: bool = False
    ) -> dict[str, Any] | list[dict[str, Any]]:
        try:
            response = await self._client.get(path, params=params)
        except httpx.RequestError as exc:
            raise CommerceClientError(
                "transport_error", "Commerce service is unavailable", retryable=True
            ) from exc

        if response.status_code >= 400:
            if response.status_code == 404:
                code = "not_found"
            elif response.status_code == 422:
                code = "validation_error"
            elif response.status_code == 409:
                code = "business_conflict"
            elif response.status_code in (408, 429) or response.status_code >= 500:
                code = "transient_error"
            else:
                code = "http_error"
            raise CommerceClientError(
                code,
                f"Commerce request failed with HTTP {response.status_code}",
                retryable=code == "transient_error",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise CommerceClientError(
                "invalid_response", "Commerce returned invalid JSON", retryable=False
            ) from exc
        if is_list:
            valid = isinstance(data, list) and all(
                isinstance(item, dict) for item in data
            )
        else:
            valid = isinstance(data, dict)
        if not valid:
            raise CommerceClientError(
                "invalid_response", "Commerce returned an unexpected result", retryable=False
            )
        return data

    async def list_products(self) -> list[dict[str, Any]]:
        """List product IDs and names for product discovery."""
        return cast(list[dict[str, Any]], await self._get("/products", is_list=True))

    async def get_product(self, product_id: str) -> dict[str, Any]:
        """Get a product and all its variants."""
        path = self._path_id(product_id)
        return cast(dict[str, Any], await self._get(f"/products/{path}"))

    async def get_item(self, item_id: str) -> dict[str, Any]:
        """Get one variant's current price, options, and availability."""
        path = self._path_id(item_id)
        return cast(dict[str, Any], await self._get(f"/items/{path}"))

    async def list_orders(self) -> list[dict[str, Any]]:
        """List orders owned by the authenticated user context."""
        user_id = self._path_id(self.user_id)
        return cast(
            list[dict[str, Any]],
            await self._get(f"/customers/{user_id}/orders", is_list=True),
        )

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Get one order, scoped to the authenticated user context."""
        path = self._path_id(order_id)
        return cast(
            dict[str, Any],
            await self._get(f"/orders/{path}", params={"user_id": self.user_id}),
        )

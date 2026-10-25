"""Commerce read tools call the expected HTTP endpoints and classify failures."""

import httpx
import pytest

from services.agent_service.app.transport.commer_client import (
    CommerceClient,
    CommerceClientError,
)


@pytest.mark.asyncio
async def test_read_operations_use_commerce_routes_and_current_user() -> None:
    requests: list[tuple[str, str, str | None]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(
            (
                request.method,
                request.url.path,
                request.url.params.get("user_id")
                or request.url.params.get("customer_id"),
            )
        )
        if request.url.path in ("/products", "/customers/u1/orders", "/after-sales/"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"ok": True})

    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        assert await client.list_products() == []
        assert await client.get_product("p1") == {"ok": True}
        assert await client.get_item("sku1") == {"ok": True}
        assert await client.list_orders() == []
        assert await client.get_order("o1") == {"ok": True}
        assert await client.list_after_sales_cases() == []
        assert await client.get_after_sales_case("c1") == {"ok": True}

    assert requests == [
        ("GET", "/products", None),
        ("GET", "/products/p1", None),
        ("GET", "/items/sku1", None),
        ("GET", "/customers/u1/orders", None),
        ("GET", "/orders/o1", "u1"),
        ("GET", "/after-sales/", "u1"),
        ("GET", "/after-sales/c1", "u1"),
    ]


@pytest.mark.asyncio
async def test_http_errors_are_classified() -> None:
    async with CommerceClient(
        "http://commerce.test",
        "u1",
        transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
    ) as client:
        with pytest.raises(CommerceClientError) as error:
            await client.get_order("missing")
    assert error.value.code == "not_found"
    assert error.value.status_code == 404
    assert not error.value.retryable


@pytest.mark.asyncio
async def test_transport_errors_and_invalid_responses_are_classified() -> None:
    def disconnect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("disconnected", request=request)

    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(disconnect)
    ) as client:
        with pytest.raises(CommerceClientError) as error:
            await client.list_products()
    assert error.value.code == "transport_error"
    assert error.value.retryable

    async with CommerceClient(
        "http://commerce.test",
        "u1",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
    ) as client:
        with pytest.raises(CommerceClientError) as error:
            await client.list_products()
    assert error.value.code == "invalid_response"
    assert not error.value.retryable


@pytest.mark.asyncio
async def test_direct_client_calls_forward_ids_without_tool_validation() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={})

    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        assert await client.get_product("p1") == {}
        assert await client.get_item("sku1") == {}
        assert await client.get_order("o1") == {}

    assert requests == ["/products/p1", "/items/sku1", "/orders/o1"]

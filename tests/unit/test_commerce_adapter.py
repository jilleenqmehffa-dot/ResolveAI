"""Commerce snapshots give the evaluator observable task state over HTTP."""

import json

import httpx
import pytest

from evals.adapter.commerce_adapter import CommerceAdapter, CommerceAdapterError


@pytest.mark.asyncio
async def test_capture_task_reads_all_customer_orders_and_expected_resources() -> None:
    requests: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["x-agent-token"] == "test-token"
        assert request.headers["x-customer-id"] == "u1"
        path = request.url.path
        requests.append((path, str(request.url.query)))
        if path == "/customers/u1":
            return httpx.Response(200, json={"user_id": "u1", "address": {}})
        if path == "/customers/u1/payment-methods":
            return httpx.Response(
                200, json=[{"payment_method_id": "gift1", "balance": "5.00"}]
            )
        if path == "/customers/u1/orders":
            return httpx.Response(200, json=[{"order_id": "#W1"}, {"order_id": "#W2"}])
        if path.startswith("/orders/"):
            if path.endswith("missing"):
                return httpx.Response(404)
            return httpx.Response(200, json={"order_id": path.removeprefix("/orders/")})
        if path.startswith("/products/"):
            return (
                httpx.Response(404)
                if path.endswith("missing")
                else httpx.Response(200, json={"product_id": "p1", "variants": []})
            )
        if path.startswith("/items/"):
            return httpx.Response(200, json={"item_id": path.removeprefix("/items/")})
        if path == "/customers/lookup/by-email":
            assert request.url.params["email"] == "ada@example.com"
            return httpx.Response(200, json={"user_id": "u1"})
        if path == "/customers/lookup/by-name-zip":
            assert request.url.params["zip"] == "19122"
            return httpx.Response(200, json={"user_id": "u1"})
        raise AssertionError(f"Unexpected request: {request.url}")

    task = {
        "id": "17",
        "evaluation_criteria": {
            "actions": [
                {
                    "action_id": "17_0",
                    "name": "find_user_id_by_email",
                    "arguments": {"email": "ada@example.com"},
                },
                {
                    "action_id": "17_1",
                    "name": "find_user_id_by_name_zip",
                    "arguments": {
                        "first_name": "Ada",
                        "last_name": "Lin",
                        "zip": "19122",
                    },
                },
                {
                    "action_id": "17_2",
                    "name": "modify_pending_order_items",
                    "arguments": {
                        "order_id": "#W1",
                        "item_ids": ["old"],
                        "new_item_ids": ["new"],
                    },
                },
                {
                    "action_id": "17_3",
                    "name": "get_order_details",
                    "arguments": {"order_id": "missing"},
                },
                {
                    "action_id": "17_4",
                    "name": "get_product_details",
                    "arguments": {"product_id": "p1"},
                },
                {
                    "action_id": "17_5",
                    "name": "get_product_details",
                    "arguments": {"product_id": "missing"},
                },
            ]
        },
    }
    async with CommerceAdapter(
        "u1",
        base_url="http://commerce.test",
        agent_token="test-token",
        transport=httpx.MockTransport(respond),
    ) as adapter:
        snapshot = await adapter.capture_task(task)

    assert snapshot.task_id == "17"
    assert snapshot.customer["user_id"] == "u1"
    assert snapshot.payment_methods[0]["balance"] == "5.00"
    assert set(snapshot.orders) == {"#W1", "#W2", "missing"}
    assert snapshot.orders["missing"] is None
    assert snapshot.products["missing"] is None
    assert set(snapshot.items) == {"old", "new"}
    assert snapshot.identity_lookups == {"17_0": "u1", "17_1": "u1"}
    assert len([path for path, _ in requests if path.startswith("/orders/")]) == 3


@pytest.mark.asyncio
async def test_authentication_errors_do_not_look_like_missing_resources() -> None:
    async with CommerceAdapter(
        "u1",
        base_url="http://commerce.test",
        agent_token="test-token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(403)),
    ) as adapter:
        with pytest.raises(CommerceAdapterError) as error:
            await adapter.get_order("o1")
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_task_cannot_query_another_customer() -> None:
    async with CommerceAdapter(
        "u1",
        base_url="http://commerce.test",
        agent_token="test-token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
    ) as adapter:
        with pytest.raises(ValueError, match="different customer"):
            await adapter.capture_task(
                {
                    "id": "1",
                    "evaluation_criteria": {
                        "actions": [
                            {
                                "name": "get_user_details",
                                "arguments": {"user_id": "u2"},
                            }
                        ]
                    },
                }
            )


@pytest.mark.asyncio
async def test_bench_write_methods_forward_task_arguments_and_identity() -> None:
    requests: list[tuple[str, dict]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["x-agent-token"] == "test-token"
        assert request.headers["x-customer-id"] == "u1"
        body = json.loads(request.content)
        assert "user_id" not in body
        requests.append((request.url.path, body))
        return httpx.Response(200, json={"ok": True})

    address = {
        "address1": "Street",
        "address2": "",
        "city": "City",
        "state": "ST",
        "country": "USA",
        "zip": "12345",
    }
    async with CommerceAdapter(
        "u1",
        base_url="http://commerce.test",
        agent_token="test-token",
        transport=httpx.MockTransport(respond),
    ) as adapter:
        assert await adapter.modify_pending_order_items(
            "#W1", ["a"], ["b"], "card1"
        ) == {"ok": True}
        await adapter.exchange_delivered_order_items("#W1", ["a"], ["b"], "card1")
        await adapter.return_delivered_order_items("#W1", ["a"], "card1")
        await adapter.cancel_pending_order("#W1", "reason")
        await adapter.modify_pending_order_address("#W1", **address)
        await adapter.modify_pending_order_payment("#W1", "card1")
        await adapter.modify_user_address("u1", **address)

    assert requests == [
        (
            "/orders/#W1/items/modify",
            {"item_ids": ["a"], "new_item_ids": ["b"], "payment_method_id": "card1"},
        ),
        (
            "/orders/#W1/exchange",
            {"item_ids": ["a"], "new_item_ids": ["b"], "payment_method_id": "card1"},
        ),
        ("/orders/#W1/return", {"item_ids": ["a"], "payment_method_id": "card1"}),
        ("/orders/#W1/cancel", {"reason": "reason"}),
        ("/orders/#W1/address", address),
        ("/orders/#W1/payment", {"payment_method_id": "card1"}),
        ("/customers/u1/address", address),
    ]


@pytest.mark.asyncio
async def test_bench_write_surfaces_commerce_validation_error() -> None:
    async with CommerceAdapter(
        "u1",
        base_url="http://commerce.test",
        agent_token="test-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(404, json={"detail": "Order was not found"})
        ),
    ) as adapter:
        with pytest.raises(CommerceAdapterError) as error:
            await adapter.cancel_pending_order("#W2", "reason")
    assert error.value.status_code == 404
    assert error.value.path == "/orders/%23W2/cancel"
    assert str(error.value) == "Order was not found"

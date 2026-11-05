"""Access Commerce state and explicit Bench writes through its HTTP API.

Snapshot capture never executes the task's expected actions; a later evaluator
compares a snapshot with the task rubric.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self, cast
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _setting(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        value = dotenv_values(ENV_FILE).get(name)
    return value.strip() if value else None


class CommerceAdapterError(RuntimeError):
    """A Commerce request failed for a reason other than an allowed missing resource."""

    def __init__(self, path: str, status_code: int | None, detail: str) -> None:
        super().__init__(detail)
        self.path = path
        self.status_code = status_code


@dataclass(frozen=True)
class CommerceSnapshot:
    """One customer's Commerce state relevant to a Bench task."""

    task_id: str
    customer_id: str
    captured_at: datetime
    customer: dict[str, Any]
    payment_methods: list[dict[str, Any]]
    order_summaries: list[dict[str, Any]]
    orders: dict[str, dict[str, Any] | None]
    products: dict[str, dict[str, Any] | None]
    items: dict[str, dict[str, Any] | None]
    identity_lookups: dict[str, str | None]


class CommerceAdapter:
    """Call Commerce with the Bench customer's authenticated Agent context."""

    def __init__(
        self,
        customer_id: str,
        *,
        base_url: str | None = None,
        agent_token: str | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = base_url or _setting("COMMERCE_BASE_URL")
        agent_token = agent_token or _setting("AGENT_INTERNAL_TOKEN")
        if not customer_id.strip():
            raise ValueError("customer_id is required")
        if not base_url:
            raise ValueError("COMMERCE_BASE_URL is required")
        if not agent_token:
            raise ValueError("AGENT_INTERNAL_TOKEN is required")
        self.customer_id = customer_id
        self._headers = {
            "X-Agent-Token": agent_token,
            "X-Customer-ID": customer_id,
        }
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        is_list: bool = False,
        allow_missing: bool = False,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        try:
            response = await self._client.request(
                method, path, params=params, json=json_body, headers=self._headers
            )
        except httpx.RequestError as exc:
            raise CommerceAdapterError(path, None, "Commerce is unavailable") from exc
        if method == "GET" and response.status_code == 404 and allow_missing:
            return None
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise CommerceAdapterError(
                path,
                response.status_code,
                str(detail)
                if detail
                else f"Commerce request failed with HTTP {response.status_code}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise CommerceAdapterError(
                path, response.status_code, "Invalid JSON"
            ) from exc
        valid = (
            isinstance(data, list) and all(isinstance(item, dict) for item in data)
            if is_list
            else isinstance(data, dict)
        )
        if not valid:
            raise CommerceAdapterError(
                path, response.status_code, "Unexpected Commerce response shape"
            )
        return data

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        is_list: bool = False,
        allow_missing: bool = False,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        return await self._request(
            "GET", path, params=params, is_list=is_list, allow_missing=allow_missing
        )

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request("POST", path, json_body=body))

    async def _order_write(
        self, order_id: str, suffix: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._post(f"/orders/{quote(order_id, safe='')}/{suffix}", body)

    async def modify_pending_order_items(
        self,
        order_id: str,
        item_ids: list[str],
        new_item_ids: list[str],
        payment_method_id: str,
    ) -> dict[str, Any]:
        return await self._order_write(
            order_id,
            "items/modify",
            {
                "item_ids": item_ids,
                "new_item_ids": new_item_ids,
                "payment_method_id": payment_method_id,
            },
        )

    async def exchange_delivered_order_items(
        self,
        order_id: str,
        item_ids: list[str],
        new_item_ids: list[str],
        payment_method_id: str,
    ) -> dict[str, Any]:
        return await self._order_write(
            order_id,
            "exchange",
            {
                "item_ids": item_ids,
                "new_item_ids": new_item_ids,
                "payment_method_id": payment_method_id,
            },
        )

    async def return_delivered_order_items(
        self,
        order_id: str,
        item_ids: list[str],
        payment_method_id: str,
    ) -> dict[str, Any]:
        return await self._order_write(
            order_id,
            "return",
            {
                "item_ids": item_ids,
                "payment_method_id": payment_method_id,
            },
        )

    async def cancel_pending_order(self, order_id: str, reason: str) -> dict[str, Any]:
        return await self._order_write(order_id, "cancel", {"reason": reason})

    async def modify_pending_order_address(
        self,
        order_id: str,
        address1: str,
        address2: str,
        city: str,
        state: str,
        country: str,
        zip: str,
    ) -> dict[str, Any]:
        return await self._order_write(
            order_id,
            "address",
            {
                "address1": address1,
                "address2": address2,
                "city": city,
                "state": state,
                "country": country,
                "zip": zip,
            },
        )

    async def modify_pending_order_payment(
        self,
        order_id: str,
        payment_method_id: str,
    ) -> dict[str, Any]:
        return await self._order_write(
            order_id,
            "payment",
            {
                "payment_method_id": payment_method_id,
            },
        )

    async def modify_user_address(
        self,
        user_id: str,
        address1: str,
        address2: str,
        city: str,
        state: str,
        country: str,
        zip: str,
    ) -> dict[str, Any]:
        return await self._post(
            f"/customers/{quote(user_id, safe='')}/address",
            {
                "address1": address1,
                "address2": address2,
                "city": city,
                "state": state,
                "country": country,
                "zip": zip,
            },
        )

    async def find_customer_by_email(self, email: str) -> str | None:
        data = await self._get(
            "/customers/lookup/by-email",
            params={"email": email},
            allow_missing=True,
        )
        return cast(dict[str, Any], data)["user_id"] if data is not None else None

    async def find_customer_by_name_zip(
        self, first_name: str, last_name: str, zip_code: str
    ) -> str | None:
        data = await self._get(
            "/customers/lookup/by-name-zip",
            params={
                "first_name": first_name,
                "last_name": last_name,
                "zip": zip_code,
            },
            allow_missing=True,
        )
        return cast(dict[str, Any], data)["user_id"] if data is not None else None

    async def get_customer(self) -> dict[str, Any]:
        path = quote(self.customer_id, safe="")
        return cast(dict[str, Any], await self._get(f"/customers/{path}"))

    async def list_payment_methods(self) -> list[dict[str, Any]]:
        path = quote(self.customer_id, safe="")
        return cast(
            list[dict[str, Any]],
            await self._get(f"/customers/{path}/payment-methods", is_list=True),
        )

    async def list_orders(self) -> list[dict[str, Any]]:
        path = quote(self.customer_id, safe="")
        return cast(
            list[dict[str, Any]],
            await self._get(f"/customers/{path}/orders", is_list=True),
        )

    async def get_order(self, order_id: str) -> dict[str, Any] | None:
        path = quote(order_id, safe="")
        return cast(
            dict[str, Any] | None,
            await self._get(f"/orders/{path}", allow_missing=True),
        )

    async def get_product(self, product_id: str) -> dict[str, Any] | None:
        path = quote(product_id, safe="")
        return cast(
            dict[str, Any] | None,
            await self._get(f"/products/{path}", allow_missing=True),
        )

    async def get_item(self, item_id: str) -> dict[str, Any] | None:
        path = quote(item_id, safe="")
        return cast(
            dict[str, Any] | None,
            await self._get(f"/items/{path}", allow_missing=True),
        )

    async def capture_task(self, task: dict[str, Any]) -> CommerceSnapshot:
        """Read current state for all resources referenced by a Bench task.

        All of the customer's orders are included so a scorer can also detect
        unintended changes to orders omitted from the expected action list.
        """
        actions = task["evaluation_criteria"]["actions"]
        task_id = str(task["id"])
        order_ids: set[str] = set()
        product_ids: set[str] = set()
        item_ids: set[str] = set()
        lookup_actions: list[dict[str, Any]] = []
        for action in actions:
            args = action["arguments"]
            if args.get("user_id") and args["user_id"] != self.customer_id:
                raise ValueError("Task references a different customer")
            if "order_id" in args:
                order_ids.add(args["order_id"])
            if "product_id" in args:
                product_ids.add(args["product_id"])
            if "item_id" in args:
                item_ids.add(args["item_id"])
            item_ids.update(args.get("item_ids") or [])
            item_ids.update(args.get("new_item_ids") or [])
            if action["name"] in {
                "find_user_id_by_email",
                "find_user_id_by_name_zip",
            }:
                lookup_actions.append(action)

        customer, payment_methods, order_summaries = await asyncio.gather(
            self.get_customer(), self.list_payment_methods(), self.list_orders()
        )
        order_ids.update(order["order_id"] for order in order_summaries)

        async def lookup(action: dict[str, Any]) -> tuple[str, str | None]:
            args = action["arguments"]
            if action["name"] == "find_user_id_by_email":
                user_id = await self.find_customer_by_email(args["email"])
            else:
                user_id = await self.find_customer_by_name_zip(
                    args["first_name"], args["last_name"], args["zip"]
                )
            return action["action_id"], user_id

        order_keys = sorted(order_ids)
        product_keys = sorted(product_ids)
        item_keys = sorted(item_ids)
        orders, products, items, lookups = await asyncio.gather(
            asyncio.gather(*(self.get_order(key) for key in order_keys)),
            asyncio.gather(*(self.get_product(key) for key in product_keys)),
            asyncio.gather(*(self.get_item(key) for key in item_keys)),
            asyncio.gather(*(lookup(action) for action in lookup_actions)),
        )
        return CommerceSnapshot(
            task_id=task_id,
            customer_id=self.customer_id,
            captured_at=datetime.now(UTC),
            customer=customer,
            payment_methods=payment_methods,
            order_summaries=order_summaries,
            orders=dict(zip(order_keys, orders, strict=True)),
            products=dict(zip(product_keys, products, strict=True)),
            items=dict(zip(item_keys, items, strict=True)),
            identity_lookups=dict(lookups),
        )

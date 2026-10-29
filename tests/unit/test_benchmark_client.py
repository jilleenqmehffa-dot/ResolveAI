"""Check Bench mapping and bounded Agent API submission."""

import argparse
import asyncio
import json
from uuid import uuid4

import httpx

from benchmark.clients import load_client
from benchmark.clients.benchmark_client import BenchCase, load_cases


def test_every_bench_task_maps_to_one_customer_without_grading_actions():
    cases = load_cases("base")
    assert len(cases) == 114
    assert len(load_cases("train")) == 74
    assert len(load_cases("test")) == 40
    assert len({case.task_id for case in cases}) == 114
    assert all(case.customer_id and case.message.strip() for case in cases)
    assert "evaluation_criteria" not in cases[0].message


def test_concurrent_submission_uses_separate_conversations_and_keys(monkeypatch):
    active = 0
    peak = 0
    seen_keys = set()
    seen_customers = set()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        assert request.headers["x-agent-token"] == "test-token"
        seen_customers.add(request.headers["x-customer-id"])
        if request.url.path == "/conversations":
            return httpx.Response(201, json={"conversation_id": str(uuid4())})
        assert request.url.path == "/chat"
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        payload = json.loads(request.content)
        assert payload["message"]
        assert payload["idempotency_key"] not in seen_keys
        seen_keys.add(payload["idempotency_key"])
        return httpx.Response(
            202, json={"execution_id": str(uuid4()), "status": "QUEUED"}
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        load_client.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    args = argparse.Namespace(
        base_url="http://agent.test",
        timeout=2,
        concurrency=2,
        repeat=2,
        wait=False,
        poll_interval=0.01,
        wait_timeout=1,
    )
    cases = [BenchCase("1", "user-one", "first"), BenchCase("2", "user-two", "second")]
    results = asyncio.run(load_client.run(args, cases, "test-token"))

    assert len(results) == 4
    assert all(result.status == "QUEUED" and result.execution_id for result in results)
    assert len({result.conversation_id for result in results}) == 4
    assert len(seen_keys) == 4
    assert seen_customers == {"user-one", "user-two"}
    assert peak == 2

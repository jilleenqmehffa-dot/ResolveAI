"""Client-side faults retain their stage even before an execution ID exists."""

import pytest
import httpx

from evals.adapter.agent_api_adapter import submit_case
from evals.clients.benchmark_client import BenchCase


@pytest.mark.asyncio
async def test_capture_connection_failure_before_conversation_id():
    async def fail(request):
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(
        base_url="http://agent.test", transport=httpx.MockTransport(fail)
    ) as client:
        result = await submit_case(
            client, BenchCase("1", "customer", "hello"), 1, "batch", "token",
            True, 0.01, 1,
        )

    assert result.status == "ERROR"
    assert result.failure_stage == "create_conversation"
    assert result.error_type == "ConnectError"
    assert result.execution_id is None


@pytest.mark.asyncio
async def test_capture_http_status_at_chat_submission():
    async def respond(request):
        if request.url.path == "/conversations":
            return httpx.Response(201, json={"conversation_id": "conversation"})
        return httpx.Response(503, json={"detail": "unavailable"})

    async with httpx.AsyncClient(
        base_url="http://agent.test", transport=httpx.MockTransport(respond)
    ) as client:
        result = await submit_case(
            client, BenchCase("1", "customer", "hello"), 1, "batch", "token",
            True, 0.01, 1,
        )

    assert result.failure_stage == "submit_chat"
    assert result.http_status_code == 503
    assert result.error_type == "HTTPStatusError"

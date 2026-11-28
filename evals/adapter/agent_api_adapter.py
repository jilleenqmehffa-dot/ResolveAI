"""Collect Agent API submission, polling, and client-side transport failures."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from uuid import uuid4

import httpx

from evals.clients.benchmark_client import BenchCase

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}


@dataclass
class Result:
    task_id: str
    customer_id: str
    repetition: int
    conversation_id: str | None = None
    execution_id: str | None = None
    status: str = "ERROR"
    submit_seconds: float | None = None
    total_seconds: float | None = None
    error: str | None = None
    failure_stage: str | None = None
    error_type: str | None = None
    http_status_code: int | None = None
    request_customer_id: str | None = None


async def submit_case(
    client: httpx.AsyncClient,
    case: BenchCase,
    repetition: int,
    batch_id: str,
    token: str,
    wait: bool,
    poll_interval: float,
    wait_timeout: float,
    llm_backend: str = "deepseek",
    mock_mode: str = "fast",
) -> Result:
    """Return one observation even when the API fails before an execution ID exists."""
    result = Result(case.task_id, case.customer_id, repetition)
    result.request_customer_id = f"eval-anon:{uuid4().hex}"
    headers = {"X-Agent-Token": token, "X-Customer-ID": result.request_customer_id}
    started = time.monotonic()
    stage = "create_conversation"
    try:
        response = await client.post("/conversations", headers=headers)
        response.raise_for_status()
        result.conversation_id = response.json()["conversation_id"]
        stage = "submit_chat"
        response = await client.post(
            "/chat",
            headers=headers,
            json={
                "conversation_id": result.conversation_id,
                "message": case.message,
                "idempotency_key": f"bench:{batch_id}:{case.task_id}:{repetition}",
                "llm_backend": llm_backend,
                "mock_mode": mock_mode,
            },
        )
        response.raise_for_status()
        if response.status_code != 202:
            raise ValueError(f"POST /chat returned {response.status_code}, expected 202")
        data = response.json()
        result.execution_id = data["execution_id"]
        result.status = data["status"]
        result.submit_seconds = time.monotonic() - started
        if wait:
            stage = "poll_execution"
            deadline = time.monotonic() + wait_timeout
            while result.status not in TERMINAL:
                if time.monotonic() >= deadline:
                    result.status = "POLL_TIMEOUT"
                    result.failure_stage = stage
                    break
                await asyncio.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
                response = await client.get(
                    f"/executions/{result.execution_id}", headers=headers
                )
                response.raise_for_status()
                result.status = response.json()["status"]
                result.error = response.json().get("error_code")
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        result.status = "ERROR"
        result.error = str(exc)
        result.failure_stage = stage
        result.error_type = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            result.http_status_code = exc.response.status_code
    result.total_seconds = time.monotonic() - started
    return result


async def poll_existing_run(
    client: httpx.AsyncClient,
    result: Result,
    token: str,
    *,
    poll_interval: float,
    wait_timeout: float,
) -> Result:
    """Query the same AgentRun after a connection or worker has recovered."""
    if not result.execution_id or not result.request_customer_id:
        raise ValueError("an accepted execution and request identity are required")
    headers = {"X-Agent-Token": token, "X-Customer-ID": result.request_customer_id}
    deadline = time.monotonic() + wait_timeout
    while result.status not in TERMINAL:
        if time.monotonic() >= deadline:
            result.status = "POLL_TIMEOUT"
            result.failure_stage = "poll_execution"
            return result
        try:
            response = await client.get(f"/executions/{result.execution_id}", headers=headers)
            response.raise_for_status()
            payload = response.json()
            result.status = payload["status"]
            result.error = payload.get("error_code")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            result.status = "ERROR"
            result.error = str(exc)
            result.failure_stage = "poll_execution"
            result.error_type = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                result.http_status_code = exc.response.status_code
            return result
        if result.status not in TERMINAL:
            await asyncio.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    return result

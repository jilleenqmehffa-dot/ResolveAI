"""Submit Bench scenarios concurrently to the Agent API."""

from __future__ import annotations

import argparse
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
    result = Result(case.task_id, case.customer_id, repetition)
    headers = {"X-Agent-Token": token, "X-Customer-ID": case.customer_id}
    started = time.monotonic()
    try:
        response = await client.post("/conversations", headers=headers)
        response.raise_for_status()
        result.conversation_id = response.json()["conversation_id"]
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
            raise ValueError(
                f"POST /chat returned {response.status_code}, expected 202"
            )
        data = response.json()
        result.execution_id = data["execution_id"]
        result.status = data["status"]
        result.submit_seconds = time.monotonic() - started
        if wait:
            deadline = time.monotonic() + wait_timeout
            while result.status not in TERMINAL:
                if time.monotonic() >= deadline:
                    result.status = "POLL_TIMEOUT"
                    break
                await asyncio.sleep(
                    min(poll_interval, max(0, deadline - time.monotonic()))
                )
                response = await client.get(
                    f"/executions/{result.execution_id}", headers=headers
                )
                response.raise_for_status()
                result.status = response.json()["status"]
                result.error = response.json().get("error_code")
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        result.status = "ERROR"
        result.error = str(exc)
    result.total_seconds = time.monotonic() - started
    return result


async def run(
    args: argparse.Namespace, cases: list[BenchCase], token: str
) -> list[Result]:
    semaphore = asyncio.Semaphore(args.concurrency)
    batch_id = uuid4().hex
    limits = httpx.Limits(
        max_connections=args.concurrency, max_keepalive_connections=args.concurrency
    )
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=args.timeout, limits=limits
    ) as client:

        async def bounded(case: BenchCase, repetition: int) -> Result:
            async with semaphore:
                return await submit_case(
                    client,
                    case,
                    repetition,
                    batch_id,
                    token,
                    args.wait,
                    args.poll_interval,
                    args.wait_timeout,
                    getattr(args, "llm_backend", "deepseek"),
                    getattr(args, "mock_mode", "fast"),
                )

        return await asyncio.gather(
            *(
                bounded(case, repetition)
                for repetition in range(1, args.repeat + 1)
                for case in cases
            )
        )


if __name__ == "__main__":
    raise SystemExit("Use python -m evals.runner --mode concurrency (see --help)")

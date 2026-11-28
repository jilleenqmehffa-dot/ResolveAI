"""Submit Bench scenarios concurrently to the Agent API."""

from __future__ import annotations

import argparse
import asyncio
from uuid import uuid4

import httpx

from evals.clients.benchmark_client import BenchCase
from evals.adapter.agent_api_adapter import TERMINAL, Result, submit_case


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

"""Submit Bench scenarios concurrently to the Agent API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from benchmark.clients.benchmark_client import BenchCase, load_cases

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
                )

        return await asyncio.gather(
            *(
                bounded(case, repetition)
                for repetition in range(1, args.repeat + 1)
                for case in cases
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--token", help="Agent internal token; defaults to AGENT_INTERNAL_TOKEN"
    )
    parser.add_argument("--split", choices=("train", "test", "base"), default="test")
    parser.add_argument(
        "--limit", type=int, help="First N tasks from the split; default is all"
    )
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--timeout", type=float, default=10, help="HTTP request timeout in seconds"
    )
    parser.add_argument(
        "--wait", action="store_true", help="Poll executions until terminal state"
    )
    parser.add_argument("--poll-interval", type=float, default=1)
    parser.add_argument("--wait-timeout", type=float, default=180)
    parser.add_argument("--output", type=Path, help="Write one JSON result per line")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show mapped tasks without HTTP requests"
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.concurrency < 1 or args.repeat < 1 or args.timeout <= 0:
        parser.error("--concurrency, --repeat and --timeout must be positive")
    if args.poll_interval <= 0 or args.wait_timeout <= 0:
        parser.error("--poll-interval and --wait-timeout must be positive")

    cases = load_cases(args.split)
    if args.limit:
        cases = cases[: args.limit]
    if args.dry_run:
        for case in cases:
            print(json.dumps(asdict(case), ensure_ascii=False))
        print(
            f"Mapped {len(cases)} cases; {len(cases) * args.repeat} requests planned."
        )
        return 0

    load_dotenv(args.env_file, override=False)
    token = args.token or os.getenv("AGENT_INTERNAL_TOKEN")
    if not token:
        parser.error("set AGENT_INTERNAL_TOKEN in --env-file or pass --token")
    results = asyncio.run(run(args, cases, token))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as output:
            for result in results:
                output.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
    counts = Counter(result.status for result in results)
    print(
        f"Submitted {sum(r.execution_id is not None for r in results)}/{len(results)}; status: {dict(counts)}"
    )
    for result in results:
        if result.error:
            print(
                f"task {result.task_id} repetition {result.repetition}: {result.status}: {result.error}"
            )
    return (
        1
        if any(
            result.status
            in {"ERROR", "POLL_TIMEOUT", "FAILED", "CANCELLED", "TIMED_OUT"}
            for result in results
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())

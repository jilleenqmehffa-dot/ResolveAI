"""Apply a fault, collect raw Agent responses, and restore the proxy."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from uuid import uuid4

import httpx

from evals.clients.benchmark_client import BenchCase
from evals.clients.load_client import Result, submit_case
from evals.scenarios.base import Scenario, map_task
from infra.toxiproxy.control import reset_fault, set_fault


@dataclass
class NetworkFaultResult(Result):
    """A raw Agent request result with the applied fault configuration."""

    fault: str | None = None
    fault_ms: int | None = None


@contextmanager
def fault_environment(scenario: Scenario) -> Iterator[None]:
    """Apply the configured fault and restore Toxiproxy on every exit path."""
    if scenario.fault is None or scenario.fault_ms is None or scenario.toxiproxy_api is None:
        raise ValueError("network fault scenario is incomplete")
    try:
        set_fault(scenario.toxiproxy_api, scenario.fault, scenario.fault_ms)
        yield
    finally:
        reset_fault(scenario.toxiproxy_api)


async def execute_async(
    scenario: Scenario, prepared_cases: list[BenchCase], token: str,
) -> list[NetworkFaultResult]:
    """Submit requests while the proxy fault is active and retain each outcome."""
    config = scenario.inputs.config
    concurrency = scenario.concurrency
    semaphore = asyncio.Semaphore(concurrency)
    batch_id = uuid4().hex
    limits = httpx.Limits(
        max_connections=concurrency, max_keepalive_connections=concurrency,
    )
    async with httpx.AsyncClient(
        base_url=config["agent_base_url"],
        timeout=float(config.get("request_timeout", 10)), limits=limits,
    ) as client:

        async def one(case: BenchCase, repetition: int) -> NetworkFaultResult:
            async with semaphore:
                result = await submit_case(
                    client, case, repetition, batch_id, token, True,
                    float(config.get("poll_interval", 1)),
                    float(config.get("wait_timeout", 180)),
                    scenario.llm_backend,
                )
                return NetworkFaultResult(
                    **asdict(result), fault=scenario.fault, fault_ms=scenario.fault_ms,
                )

        return await asyncio.gather(*(
            one(case, repetition)
            for repetition in range(1, scenario.repeat + 1)
            for case in prepared_cases
        ))


def execute(scenario: Scenario, token: str) -> list[NetworkFaultResult]:
    if scenario.mode != "network-fault":
        raise ValueError("network-fault executor requires a network-fault scenario")
    prepared_cases = [
        map_task(task, scenario.inputs.database) for task in scenario.inputs.tasks
    ]
    with fault_environment(scenario):
        return asyncio.run(execute_async(scenario, prepared_cases, token))

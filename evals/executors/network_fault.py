"""Apply a fault, collect raw Agent responses, and restore the proxy."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from uuid import uuid4

import httpx

from evals.adapter.agent_api_adapter import Result, submit_case
from evals.adapter.agent_db_adapter import capture_agent_run
from evals.adapter.toxiproxy_adapter import ProxyObservation, capture_proxy
from evals.clients.benchmark_client import BenchCase
from evals.scenarios.base import Scenario, map_task
from infra.toxiproxy.control import reset_fault, set_fault


@dataclass
class NetworkFaultResult(Result):
    """A raw Agent request result with the applied fault configuration."""

    fault: str | None = None
    fault_ms: int | None = None
    fault_target: str = "commerce"
    proxy_observation: dict | None = None
    database_observation: dict | None = None
    fault_case: str | None = None
    outage_seconds: float | None = None
    outage_observation: dict | None = None
    checkpoint_before: dict | None = None
    checkpoint_after: dict | None = None
    tool_execution_observation: dict | None = None
    checks: dict | None = None


@contextmanager
def fault_environment(scenario: Scenario) -> Iterator[ProxyObservation]:
    """Apply the configured fault and restore Toxiproxy on every exit path."""
    if scenario.fault is None or scenario.fault_ms is None or scenario.toxiproxy_api is None:
        raise ValueError("network fault scenario is incomplete")
    try:
        set_fault(scenario.toxiproxy_api, scenario.fault, scenario.fault_ms, scenario.fault_target)
        yield capture_proxy(scenario.toxiproxy_api, scenario.fault_target)
    finally:
        reset_fault(scenario.toxiproxy_api, scenario.fault_target)


async def execute_async(
    scenario: Scenario, prepared_cases: list[BenchCase], token: str,
    proxy_observation: ProxyObservation,
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
                    fault_target=scenario.fault_target,
                    proxy_observation=proxy_observation.as_dict(),
                )

        return await asyncio.gather(*(
            one(case, repetition)
            for repetition in range(1, scenario.repeat + 1)
            for case in prepared_cases
        ))


def execute(scenario: Scenario, token: str) -> list[NetworkFaultResult]:
    if scenario.mode != "network-fault":
        raise ValueError("network-fault executor requires a network-fault scenario")
    if scenario.fault_case is not None:
        from evals.executors.fault_cases import execute as execute_case
        return execute_case(scenario, token)
    prepared_cases = [
        map_task(task, scenario.inputs.customer_oracle) for task in scenario.inputs.tasks
    ]
    with fault_environment(scenario) as proxy_observation:
        results = asyncio.run(execute_async(scenario, prepared_cases, token, proxy_observation))
        database_url = scenario.setting("AGENT_DATABASE_URL")
        if database_url:
            for result in results:
                result.database_observation = capture_agent_run(
                    database_url, result.execution_id
                ).as_dict()
        return results

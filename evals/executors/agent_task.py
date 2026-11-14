"""Run Agent tasks and capture Commerce snapshots and LangSmith traces."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from data_platform.rebuild import rebuild_database
from evals.adapter.commerce_adapter import CommerceAdapter, CommerceSnapshot
from evals.adapter.trace_adapter import AgentTrace, TraceAdapter
from evals.clients.load_client import submit_case
from evals.scenarios.base import Scenario, map_task


@dataclass
class AgentExecution:
    """One task's observations before any scoring or normalization."""

    task: dict[str, Any]
    task_id: str
    customer_id: str | None = None
    execution_id: str | None = None
    status: str = "ERROR"
    before: CommerceSnapshot | None = None
    after: CommerceSnapshot | None = None
    traces: list[AgentTrace] | None = None
    error: dict[str, str] | None = None


async def execute_task(
    task: dict[str, Any], database: dict[str, Any], config: dict[str, Any],
    client: httpx.AsyncClient, token: str, batch_id: str,
    rebuild_engine: Engine | None = None, llm_backend: str = "deepseek",
) -> AgentExecution:
    observed = AgentExecution(task=task, task_id=str(task["id"]))
    try:
        if rebuild_engine is not None:
            await asyncio.to_thread(rebuild_database, rebuild_engine)
        case = map_task(task, database)
        if not case.customer_id.strip() or not case.message.strip():
            raise ValueError("customer_id and message must be nonempty")
        observed.customer_id = case.customer_id
        async with CommerceAdapter(
            case.customer_id, base_url=config["commerce_base_url"], agent_token=token
        ) as commerce:
            observed.before = await commerce.capture_task(task)
            submitted = await submit_case(
                client, case, 1, batch_id, token, True,
                float(config.get("poll_interval", 1)),
                float(config.get("wait_timeout", 180)), llm_backend,
            )
            observed.execution_id = submitted.execution_id
            observed.status = submitted.status
            if submitted.status != "SUCCEEDED":
                raise RuntimeError(submitted.error or f"execution ended: {submitted.status}")
            observed.after = await commerce.capture_task(task)
        observed.traces = await asyncio.to_thread(
            TraceAdapter(project=config.get("langsmith_project")).wait_for_traces,
            submitted.execution_id, timeout=float(config.get("trace_timeout", 30)),
        )
        if not observed.traces:
            raise RuntimeError("no LangSmith trace found")
    except Exception as exc:  # noqa: BLE001 - keep failed tasks in the batch
        observed.error = {"type": type(exc).__name__, "message": str(exc)}
    return observed


async def execute_async(
    scenario: Scenario, token: str, rebuild_engine: Engine | None = None,
) -> list[AgentExecution]:
    if scenario.mode != "agent-task":
        raise ValueError("agent-task executor requires an agent-task scenario")
    concurrency = scenario.concurrency
    if rebuild_engine is not None and concurrency != 1:
        raise ValueError("rebuilding before each task requires concurrency=1")
    config = scenario.inputs.config
    tasks = scenario.inputs.tasks
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        base_url=config["agent_base_url"],
        timeout=float(config.get("request_timeout", 10)),
        limits=httpx.Limits(max_connections=concurrency),
    ) as client:
        batch_id = uuid4().hex
        completed = 0

        async def bounded(task: dict[str, Any]) -> AgentExecution:
            nonlocal completed
            async with semaphore:
                observed = await execute_task(
                    task, scenario.inputs.database, config, client, token, batch_id,
                    rebuild_engine, scenario.llm_backend,
                )
                completed += 1
                outcome = observed.error["type"] if observed.error else observed.status
                print(f"[{completed}/{len(tasks)}] task {observed.task_id}: {outcome}", flush=True)
                return observed

        return await asyncio.gather(*(bounded(task) for task in tasks))


def execute(scenario: Scenario, token: str) -> list[AgentExecution]:
    rebuild_engine = create_engine(scenario.test_database_url) if scenario.test_database_url else None
    try:
        return asyncio.run(execute_async(scenario, token, rebuild_engine))
    finally:
        if rebuild_engine is not None:
            rebuild_engine.dispose()

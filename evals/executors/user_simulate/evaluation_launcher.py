"""Launch complete Retail evaluations around the simulated dialogue."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx

from data_platform.rebuild import rebuild_database
from evals.adapter.commerce_adapter import CommerceAdapter
from evals.adapter.commerce_checkpoint import (
    capture_customer,
    restore_customer,
    with_engine,
)
from evals.executors.user_simulate.orchestrator import Orchestrator, State
from evals.scenarios.base import Scenario, resolve_customer_id

if TYPE_CHECKING:
    from evals.evaluators.agent_task import AgentExecution


async def launch_async(scenario: Scenario, token: str) -> list[AgentExecution]:
    """Capture Commerce state before and after each multi-turn Agent session."""
    from evals.evaluators.agent_task import AgentExecution

    if scenario.mode != "agent-task":
        raise ValueError("evaluation launcher requires an agent-task scenario")
    if scenario.concurrency != 1:
        raise ValueError("agent-task database restoration requires concurrency=1")
    database_url = scenario.test_database_url or scenario.setting("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required to restore Commerce after each task")
    config = scenario.inputs.config
    semaphore = asyncio.Semaphore(scenario.concurrency)
    async with httpx.AsyncClient(
        base_url=config["agent_base_url"],
        timeout=float(config.get("request_timeout", 10)),
        limits=httpx.Limits(max_connections=scenario.concurrency),
    ) as client:

        async def launch_one(task: dict[str, Any]) -> tuple[AgentExecution, bool]:
            observed = AgentExecution(
                task=task, task_id=str(task["id"]), eval_run_id=uuid4().hex,
            )
            restore_ok = True
            async with semaphore:
                engine = with_engine(database_url)
                checkpoint = None
                try:
                    if scenario.test_database_url:
                        await asyncio.to_thread(rebuild_database, engine)
                    customer_id = resolve_customer_id(task, scenario.inputs.customer_oracle)
                    if not customer_id.strip():
                        raise ValueError("customer_id must be nonempty")
                    observed.customer_id = customer_id
                    checkpoint = await asyncio.to_thread(capture_customer, engine, customer_id)
                    async with CommerceAdapter(
                        customer_id,
                        base_url=config["commerce_base_url"],
                        agent_token=token,
                    ) as commerce:
                        observed.before = await commerce.capture_task(task)
                        result = await Orchestrator(
                            client,
                            token,
                            llm_backend=scenario.llm_backend,
                            poll_interval=float(config.get("poll_interval", 1)),
                            execution_timeout=float(config.get("wait_timeout", 180)),
                            total_timeout=float(config.get("task_timeout", 600)),
                            max_steps=int(config.get("max_steps", 12)),
                            max_errors=int(config.get("max_errors", 2)),
                        ).run(task, eval_run_id=observed.eval_run_id)
                        observed.after = await commerce.capture_task(task)
                    observed.execution_ids = tuple(result.execution_ids)
                    observed.execution_id = result.execution_ids[0] if result.execution_ids else None
                    observed.history = tuple(result.history)
                    observed.status = result.state.value
                    if result.state in {State.ERROR, State.TIMEOUT}:
                        observed.error = {
                            "type": result.state.value,
                            "message": "; ".join(result.errors),
                        }
                except Exception as exc:  # noqa: BLE001 - keep failed tasks in the batch
                    observed.error = {"type": type(exc).__name__, "message": str(exc)}
                finally:
                    if checkpoint is not None:
                        try:
                            await asyncio.to_thread(restore_customer, engine, checkpoint)
                        except Exception as exc:  # noqa: BLE001 - restoration failure must be reported
                            restore_ok = False
                            observed.error = {
                                "type": "CommerceRestoreError",
                                "message": f"Could not restore Commerce state: {exc}",
                            }
                    engine.dispose()
            return observed, restore_ok

        results: list[AgentExecution] = []
        restore_failed = False
        for task in scenario.inputs.tasks:
            if restore_failed:
                results.append(AgentExecution(
                    task=task, task_id=str(task["id"]),
                    error={
                        "type": "CommerceRestoreError",
                        "message": "Skipped because the previous task did not restore Commerce",
                    },
                ))
                continue
            observed, restore_ok = await launch_one(task)
            results.append(observed)
            restore_failed = not restore_ok
        return results


def launch(scenario: Scenario, token: str) -> list[AgentExecution]:
    """Synchronous entry point for evals.runner."""
    return asyncio.run(launch_async(scenario, token))

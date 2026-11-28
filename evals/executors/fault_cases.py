"""Run the five named recovery cases with an outage and a recovery phase."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from uuid import uuid4

import httpx

from evals.adapter.agent_api_adapter import TERMINAL, Result, poll_existing_run, submit_case
from evals.adapter.agent_db_adapter import capture_agent_run, find_run_by_key
from evals.adapter.checkpoint_adapter import capture_checkpoint_count
from evals.adapter.tool_execution_adapter import capture_tool_executions
from evals.adapter.toxiproxy_adapter import capture_proxy
from evals.scenarios.base import Scenario, map_task
from evals.scenarios.network_fault.cases import CASES
from infra.toxiproxy.control import reset_fault, set_fault
from infra.worker_fault import start_worker, stop_worker


async def _wait_until_running(database_url: str, execution_id: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observed = await asyncio.to_thread(capture_agent_run, database_url, execution_id)
        if observed.status == "RUNNING":
            return True
        if observed.status in TERMINAL:
            return False
        await asyncio.sleep(0.1)
    return False


async def _probe_disconnected_client(
    proxy_url: str, execution_id: str, customer_id: str, token: str,
) -> dict:
    headers = {"X-Agent-Token": token, "X-Customer-ID": customer_id}
    try:
        async with httpx.AsyncClient(base_url=proxy_url, timeout=2) as client:
            response = await client.get(f"/executions/{execution_id}", headers=headers)
            response.raise_for_status()
            return {"disconnected": False, "http_status_code": response.status_code}
    except httpx.HTTPError as exc:
        return {
            "disconnected": True,
            "error_type": type(exc).__name__,
            "http_status_code": exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None,
        }


async def execute_async(scenario: Scenario, token: str):
    from evals.executors.network_fault import NetworkFaultResult

    recipe = CASES[scenario.fault_case]
    case = map_task(scenario.inputs.tasks[0], scenario.inputs.customer_oracle)
    config = scenario.inputs.config
    database_url = str(scenario.setting("AGENT_DATABASE_URL"))
    batch_id = uuid4().hex
    started = time.monotonic()
    base = Result(case.task_id, case.customer_id, 1)
    proxy_observation = None
    outage_observation = None
    checkpoint_before = None
    injected = False
    worker_stopped = False
    original_execution_id = None
    idempotency_key = f"bench:{batch_id}:{case.task_id}:1"
    idempotent_resubmit = None
    agent_base_url = (
        str(config.get("agent_proxy_url", "http://127.0.0.1:18082"))
        if scenario.fault_case == "client-disconnect" else config["agent_base_url"]
    )
    async with httpx.AsyncClient(
        base_url=agent_base_url, timeout=float(config.get("request_timeout", 10)),
    ) as client:
        try:
            if recipe.phase == "before_submit":
                await asyncio.to_thread(set_fault, scenario.toxiproxy_api, recipe.fault, 0, recipe.target)
                injected = True
                proxy_observation = (await asyncio.to_thread(
                    capture_proxy, scenario.toxiproxy_api, recipe.target
                )).as_dict()
            base = await submit_case(
                client, case, 1, batch_id, token, False, 0,
                float(config.get("wait_timeout", 180)), scenario.llm_backend, scenario.mock_mode,
            )
            original_execution_id = base.execution_id
            if recipe.phase == "after_submit":
                if not base.execution_id or not base.request_customer_id:
                    raise RuntimeError("AgentRun was not accepted before client disconnect")
                await asyncio.to_thread(set_fault, scenario.toxiproxy_api, recipe.fault, 0, recipe.target)
                injected = True
                proxy_observation = (await asyncio.to_thread(
                    capture_proxy, scenario.toxiproxy_api, recipe.target
                )).as_dict()
                outage_observation = await _probe_disconnected_client(
                    agent_base_url,
                    base.execution_id, base.request_customer_id, token,
                )
            elif recipe.phase == "after_running":
                if not base.execution_id:
                    raise RuntimeError("AgentRun was not accepted before the running-phase fault")
                running = await _wait_until_running(
                    database_url, base.execution_id,
                    float(config.get("running_timeout", 15)),
                )
                if not running:
                    raise RuntimeError("AgentRun did not enter RUNNING before the fault window")
                checkpoint_before = await asyncio.to_thread(
                    capture_checkpoint_count, database_url, base.execution_id
                )
                if scenario.fault_case == "worker-crash":
                    await asyncio.to_thread(stop_worker)
                    worker_stopped = True
                    outage_observation = {"worker_stopped": True}
                else:
                    await asyncio.to_thread(set_fault, scenario.toxiproxy_api, recipe.fault, 0, recipe.target)
                    injected = True
                    proxy_observation = (await asyncio.to_thread(
                        capture_proxy, scenario.toxiproxy_api, recipe.target
                    )).as_dict()
            await asyncio.sleep(scenario.outage_seconds)
        except Exception as exc:  # noqa: BLE001 - preserve the case result and clean up
            base.status = "ERROR"
            base.error = str(exc)
            base.error_type = type(exc).__name__
        finally:
            if injected:
                await asyncio.to_thread(reset_fault, scenario.toxiproxy_api, recipe.target)
            if worker_stopped:
                await asyncio.to_thread(start_worker)

        if base.execution_id is None and base.conversation_id is not None:
            try:
                base.execution_id = await asyncio.to_thread(
                    find_run_by_key, database_url, base.conversation_id,
                    idempotency_key,
                )
            except Exception as exc:  # noqa: BLE001 - retain the API failure
                outage_observation = {
                    **(outage_observation or {}), "run_lookup_error_type": type(exc).__name__,
                }
        if scenario.fault_case == "rabbitmq-disconnect" and base.conversation_id:
            # Retry the same submission after recovery. The API must return the
            # durable AgentRun, regardless of whether the first response arrived.
            try:
                response = await client.post(
                    "/chat",
                    headers={"X-Agent-Token": token, "X-Customer-ID": base.request_customer_id},
                    json={
                        "conversation_id": base.conversation_id,
                        "message": case.message,
                        "idempotency_key": idempotency_key,
                        "llm_backend": scenario.llm_backend,
                        "mock_mode": scenario.mock_mode,
                    },
                )
                response.raise_for_status()
                repeated_id = response.json()["execution_id"]
                idempotent_resubmit = {
                    "execution_id": repeated_id,
                    "same_run": base.execution_id is None or repeated_id == base.execution_id,
                }
                base.execution_id = base.execution_id or repeated_id
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                idempotent_resubmit = {"error_type": type(exc).__name__, "same_run": False}
            outage_observation = {**(outage_observation or {}), "idempotent_resubmit": idempotent_resubmit}
        if base.execution_id is not None and base.request_customer_id is not None:
            base.status = "QUEUED"
            await poll_existing_run(
                client, base, token,
                poll_interval=float(config.get("poll_interval", 1)),
                wait_timeout=float(config.get("wait_timeout", 180)),
            )

    database_observation = await asyncio.to_thread(capture_agent_run, database_url, base.execution_id)
    checkpoint_after = await asyncio.to_thread(
        capture_checkpoint_count, database_url, base.execution_id
    )
    tools = await asyncio.to_thread(
        capture_tool_executions, database_url,
        (base.execution_id,) if base.execution_id else (),
    )
    base.total_seconds = time.monotonic() - started
    checks = {
        "original_run_retained": base.execution_id is not None and (
            original_execution_id is None or base.execution_id == original_execution_id
        ),
        "terminal_after_recovery": base.status in TERMINAL,
        "database_matches_api": database_observation.status == base.status,
    }
    if scenario.fault_case == "client-disconnect":
        checks["client_disconnected_during_outage"] = bool(
            outage_observation and outage_observation.get("disconnected")
        )
    if scenario.fault_case == "commerce-disconnect":
        checks["tool_retry_observed"] = any(
            row["attempt_count"] > 1 for row in tools["records"]
        )
    if scenario.fault_case == "rabbitmq-disconnect":
        checks["idempotent_resubmit_same_run"] = bool(
            idempotent_resubmit and idempotent_resubmit.get("same_run")
            and idempotent_resubmit.get("execution_id") == base.execution_id
        )
    if scenario.fault_case == "worker-crash":
        checks["worker_reclaimed_run"] = bool(
            database_observation.attempt_count and database_observation.attempt_count > 1
        )
    if scenario.fault_case in {"worker-crash", "postgres-disconnect"}:
        checks["checkpoint_persisted"] = (
            checkpoint_after["count"] is not None and checkpoint_after["count"] > 0
            and (checkpoint_before["count"] or 0) <= checkpoint_after["count"]
        ) if checkpoint_before is not None else False
    return [NetworkFaultResult(
        **asdict(base), fault=recipe.fault, fault_ms=0 if recipe.fault else None,
        fault_target=recipe.target,
        proxy_observation=proxy_observation,
        database_observation=database_observation.as_dict(),
        fault_case=scenario.fault_case, outage_seconds=scenario.outage_seconds,
        outage_observation=outage_observation,
        checkpoint_before=checkpoint_before, checkpoint_after=checkpoint_after,
        tool_execution_observation=tools, checks=checks,
    )]


def execute(scenario: Scenario, token: str):
    return asyncio.run(execute_async(scenario, token))

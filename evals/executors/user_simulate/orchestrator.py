"""Drive a multi-turn simulated customer through the Agent Service API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import httpx

from evals.clients.load_client import TERMINAL
from evals.executors.user_simulate.user_executor import generate_next_user_message

Simulator = Callable[[dict[str, Any], str | None, list[tuple[str, str]]], Awaitable[str]]


class State(StrEnum):
    USER = "USER"
    AGENT = "AGENT"
    STOPPED = "STOPPED"
    STEP_LIMIT = "STEP_LIMIT"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


@dataclass
class OrchestrationResult:
    task_id: str
    principal_id: str
    eval_run_id: str
    state: State = State.USER
    conversation_id: str | None = None
    execution_ids: list[str] = field(default_factory=list)
    history: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def _default_simulator(
    task: dict[str, Any], agent_reply: str | None, history: list[tuple[str, str]],
) -> str:
    return await generate_next_user_message(task, agent_reply, history=history)


class Orchestrator:
    """Switch between user generation and Agent turns; Agent owns Commerce tools."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        token: str,
        *,
        simulator: Simulator = _default_simulator,
        llm_backend: str = "deepseek",
        poll_interval: float = 1.0,
        execution_timeout: float = 180.0,
        total_timeout: float = 600.0,
        max_steps: int = 12,
        max_errors: int = 2,
    ) -> None:
        if not token or not 0 < poll_interval or not 0 < execution_timeout or not 0 < total_timeout:
            raise ValueError("token and all timeouts/intervals must be positive")
        if max_steps < 1 or max_errors < 1:
            raise ValueError("max_steps and max_errors must be positive")
        self.client = client
        self.token = token
        self.simulator = simulator
        self.llm_backend = llm_backend
        self.poll_interval = poll_interval
        self.execution_timeout = execution_timeout
        self.total_timeout = total_timeout
        self.max_steps = max_steps
        self.max_errors = max_errors

    async def run(
        self, task: dict[str, Any], *, eval_run_id: str | None = None,
    ) -> OrchestrationResult:
        """Run one task and return the dialogue plus Agent execution IDs for scoring."""
        if "id" not in task:
            raise ValueError("task.id is required")
        run_id = UUID(eval_run_id).hex if eval_run_id is not None else uuid4().hex
        principal_id = f"eval-anon:{run_id}"
        result = OrchestrationResult(str(task["id"]), principal_id, run_id)
        headers = {"X-Agent-Token": self.token, "X-Customer-ID": principal_id}
        errors = 0
        agent_reply: str | None = None
        try:
            async with asyncio.timeout(self.total_timeout):
                while result.state in {State.USER, State.AGENT}:
                    try:
                        if result.state is State.USER:
                            message = await self.simulator(task, agent_reply, list(result.history))
                            if not isinstance(message, str) or not message.strip():
                                raise ValueError("user simulator returned an empty message")
                            message = message.strip()
                            if message == "STOP":
                                result.state = State.STOPPED
                            elif sum(role == "agent" for role, _ in result.history) >= self.max_steps:
                                result.state = State.STEP_LIMIT
                            else:
                                result.history.append(("user", message))
                                result.state = State.AGENT
                        else:
                            if result.conversation_id is None:
                                response = await self.client.post("/conversations", headers=headers)
                                response.raise_for_status()
                                result.conversation_id = response.json()["conversation_id"]
                            reply = await self._agent_turn(result, headers, run_id)
                            result.history.append(("agent", reply))
                            agent_reply = reply
                            result.state = State.USER
                        errors = 0
                    except TimeoutError as exc:
                        result.errors.append(str(exc))
                        result.state = State.TIMEOUT
                    except Exception as exc:  # noqa: BLE001 - preserve failed tasks for the runner
                        result.errors.append(f"{type(exc).__name__}: {exc}")
                        errors += 1
                        if errors >= self.max_errors:
                            result.state = State.ERROR
        except TimeoutError:
            result.state = State.TIMEOUT
            result.errors.append("conversation exceeded total_timeout")
        return result

    async def _agent_turn(
        self, result: OrchestrationResult, headers: dict[str, str], run_id: str,
    ) -> str:
        turn = sum(role == "agent" for role, _ in result.history)
        response = await self.client.post(
            "/chat", headers=headers,
            json={
                "conversation_id": result.conversation_id,
                "message": result.history[-1][1],
                "idempotency_key": f"user-sim:{run_id}:{turn}",
                "llm_backend": self.llm_backend,
            },
        )
        response.raise_for_status()
        if response.status_code != 202:
            raise ValueError(f"POST /chat returned {response.status_code}, expected 202")
        data = response.json()
        execution_id = str(data["execution_id"])
        if execution_id not in result.execution_ids:
            result.execution_ids.append(execution_id)
        deadline = time.monotonic() + self.execution_timeout
        while data["status"] not in TERMINAL:
            if time.monotonic() >= deadline:
                raise TimeoutError("Agent execution exceeded execution_timeout")
            await asyncio.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
            response = await self.client.get(f"/executions/{execution_id}", headers=headers)
            response.raise_for_status()
            data = response.json()
        if data["status"] != "SUCCEEDED":
            raise ValueError(f"Agent execution {execution_id} ended: {data['status']} ({data.get('error_code')})")
        reply = data.get("result")
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError(f"Agent execution {execution_id} returned no reply")
        return reply.strip()

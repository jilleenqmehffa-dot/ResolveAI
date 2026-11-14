"""Interactive Locust load test for the Agent execution API."""

from __future__ import annotations

import os
import random
import time
from uuid import uuid4

from gevent import sleep
from locust import HttpUser, constant, task

from evals.clients.benchmark_client import load_cases

CASES = load_cases(os.getenv("LOCUST_SPLIT", "test"))
TOKEN = os.getenv("AGENT_INTERNAL_TOKEN")
BACKEND = os.getenv("LOCUST_LLM_BACKEND", "mock")
MOCK_MODE = os.getenv("LOCUST_MOCK_MODE", "fast")
REQUEST_TIMEOUT = float(os.getenv("LOCUST_REQUEST_TIMEOUT", "10"))
POLL_INTERVAL = float(os.getenv("LOCUST_POLL_INTERVAL", "1"))
WAIT_TIMEOUT = float(os.getenv("LOCUST_WAIT_TIMEOUT", "180"))
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}

if not TOKEN:
    raise RuntimeError("AGENT_INTERNAL_TOKEN is required for Locust")
if BACKEND not in {"mock", "deepseek"} or MOCK_MODE not in {"fast", "latency"}:
    raise ValueError("invalid Locust backend or mock mode")
if BACKEND != "mock" and MOCK_MODE != "fast":
    raise ValueError("latency mock mode requires the mock backend")
if min(REQUEST_TIMEOUT, POLL_INTERVAL, WAIT_TIMEOUT) <= 0:
    raise ValueError("Locust request and poll timeouts must be positive")


class AgentUser(HttpUser):
    """Run one randomly selected task per user iteration."""

    wait_time = constant(0)

    def request_json(
        self, method: str, path: str, headers: dict[str, str], *, name: str,
        expected: int = 200, payload: dict | None = None,
    ) -> dict:
        with self.client.request(
            method, path, name=name, headers=headers, json=payload,
            timeout=REQUEST_TIMEOUT, catch_response=True,
        ) as response:
            if response.status_code != expected:
                response.failure(f"HTTP {response.status_code}; expected {expected}")
                raise ValueError(f"{name} returned HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError as exc:
                response.failure("invalid JSON response")
                raise ValueError(f"{name} returned invalid JSON") from exc

    @task
    def run_agent_task(self) -> None:
        case = random.choice(CASES)
        headers = {"X-Agent-Token": TOKEN, "X-Customer-ID": case.customer_id}
        started = time.monotonic()
        error = None
        try:
            conversation = self.request_json(
                "POST", "/conversations", headers, name="POST /conversations", expected=201,
            )
            submission = self.request_json(
                "POST", "/chat", headers, name="POST /chat", expected=202,
                payload={
                    "conversation_id": conversation["conversation_id"],
                    "message": case.message,
                    "idempotency_key": f"locust:{uuid4().hex}",
                    "llm_backend": BACKEND,
                    "mock_mode": MOCK_MODE,
                },
            )
            status = submission["status"]
            execution_id = submission["execution_id"]
            deadline = time.monotonic() + WAIT_TIMEOUT
            while status not in TERMINAL:
                if time.monotonic() >= deadline:
                    raise TimeoutError("execution polling timed out")
                sleep(min(POLL_INTERVAL, max(0, deadline - time.monotonic())))
                execution = self.request_json(
                    "GET", f"/executions/{execution_id}", headers,
                    name="GET /executions/:id",
                )
                status = execution["status"]
                if status != "SUCCEEDED" and status in TERMINAL:
                    raise RuntimeError(execution.get("error_code") or status)
            if status != "SUCCEEDED":
                raise RuntimeError(status)
        except Exception as exc:  # noqa: BLE001 - mark this flow failed and keep the load running
            error = exc
        finally:
            self.environment.events.request.fire(
                request_type="FLOW", name="agent execution",
                response_time=(time.monotonic() - started) * 1000, response_length=0,
                exception=error, context={},
            )

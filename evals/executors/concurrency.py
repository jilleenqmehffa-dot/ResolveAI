"""Run the fixed concurrency scenario with headless Locust users."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from evals.clients.load_client import TERMINAL, Result
from evals.scenarios.base import Scenario, map_task


def execute(scenario: Scenario, token: str) -> list[Result]:
    """Keep the runner's per-task result format while Locust drives the load."""
    if scenario.mode != "concurrency":
        raise ValueError("concurrency executor requires a concurrency scenario")
    config = scenario.inputs.config
    workload = {
        "host": config["agent_base_url"],
        "cases": [asdict(map_task(task, scenario.inputs.database)) for task in scenario.inputs.tasks],
        "concurrency": scenario.concurrency,
        "repeat": scenario.repeat,
        "request_timeout": float(config.get("request_timeout", 10)),
        "poll_interval": float(config.get("poll_interval", 1)),
        "wait_timeout": float(config.get("wait_timeout", 180)),
        "llm_backend": scenario.llm_backend,
        "mock_mode": scenario.mock_mode,
    }
    if min(workload[key] for key in ("request_timeout", "poll_interval", "wait_timeout")) <= 0:
        raise ValueError("request_timeout, poll_interval and wait_timeout must be positive")
    with tempfile.TemporaryDirectory(prefix="resolveai-locust-") as directory:
        source = Path(directory) / "workload.json"
        destination = Path(directory) / "result.json"
        source.write_text(json.dumps(workload), encoding="utf-8")
        environment = os.environ.copy()
        environment["RESOLVEAI_LOAD_TOKEN"] = token
        count = len(workload["cases"]) * scenario.repeat
        batches = (count + scenario.concurrency - 1) // scenario.concurrency
        timeout = batches * (workload["wait_timeout"] + 3 * workload["request_timeout"] + 10) + 30
        completed = subprocess.run(
            [sys.executable, "-m", "evals.locust_worker", str(source), str(destination)],
            env=environment, capture_output=True, text=True, check=False, timeout=timeout,
        )
        if completed.returncode:
            raise RuntimeError(f"Locust run failed: {completed.stderr.strip()[-2000:]}")
        payload = json.loads(destination.read_text(encoding="utf-8"))
    metrics = payload["metrics"]
    print(
        f"Locust: {metrics['users']} users, {metrics['flows']} executions, "
        f"{metrics['http_rps']:.2f} HTTP req/s, "
        f"execution p95 {metrics['execution_p95_ms']:.0f} ms"
    )
    return [Result(**row) for row in payload["results"]]


def run_locust(workload: dict, token: str) -> dict:
    """Execute a finite workload; called after gevent patching in the worker."""
    import gevent
    from gevent.event import Event
    from gevent.queue import Empty, Queue
    from locust import HttpUser, constant, task
    from locust.env import Environment
    from locust.exception import StopUser

    jobs = Queue()
    for repetition in range(1, workload["repeat"] + 1):
        for case in workload["cases"]:
            jobs.put((jobs.qsize(), case, repetition))
    count = jobs.qsize()
    results: list[dict | None] = [None] * count
    finished = Event()
    remaining = count
    batch_id = uuid4().hex

    class AgentUser(HttpUser):
        host = workload["host"]
        wait_time = constant(0)

        def request_json(
            self, method: str, path: str, headers: dict, *, name: str,
            expected: int = 200, payload: dict | None = None,
        ) -> dict:
            with self.client.request(
                method, path, name=name, headers=headers, json=payload,
                timeout=workload["request_timeout"], catch_response=True,
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
        def execute_case(self) -> None:
            nonlocal remaining
            try:
                index, case, repetition = jobs.get_nowait()
            except Empty:
                raise StopUser() from None
            result = Result(case["task_id"], case["customer_id"], repetition)
            headers = {"X-Agent-Token": token, "X-Customer-ID": case["customer_id"]}
            started = time.monotonic()
            try:
                conversation = self.request_json(
                    "POST", "/conversations", headers, name="POST /conversations", expected=201,
                )
                result.conversation_id = conversation["conversation_id"]
                submission = self.request_json(
                    "POST", "/chat", headers, name="POST /chat", expected=202,
                    payload={
                        "conversation_id": result.conversation_id,
                        "message": case["message"],
                        "idempotency_key": f"bench:{batch_id}:{index}",
                        "llm_backend": workload["llm_backend"],
                        "mock_mode": workload["mock_mode"],
                    },
                )
                result.execution_id = submission["execution_id"]
                result.status = submission["status"]
                result.submit_seconds = time.monotonic() - started
                deadline = time.monotonic() + workload["wait_timeout"]
                while result.status not in TERMINAL:
                    if time.monotonic() >= deadline:
                        result.status = "POLL_TIMEOUT"
                        break
                    gevent.sleep(min(workload["poll_interval"], max(0, deadline - time.monotonic())))
                    execution = self.request_json(
                        "GET", f"/executions/{result.execution_id}", headers,
                        name="GET /executions/:id",
                    )
                    result.status = execution["status"]
                    result.error = execution.get("error_code")
            except Exception as exc:  # noqa: BLE001 - continue other load jobs
                result.status = "ERROR"
                result.error = str(exc)
            finally:
                result.total_seconds = time.monotonic() - started
                self.environment.events.request.fire(
                    request_type="FLOW", name="agent execution",
                    response_time=result.total_seconds * 1000, response_length=0,
                    exception=None if result.status == "SUCCEEDED" else RuntimeError(result.error or result.status),
                    context={},
                )
                results[index] = asdict(result)
                remaining -= 1
                if remaining == 0:
                    finished.set()

    environment = Environment(user_classes=[AgentUser], host=workload["host"])
    runner = environment.create_local_runner()
    started = time.monotonic()
    try:
        runner.start(min(workload["concurrency"], count), spawn_rate=workload["concurrency"])
        finished.wait()
    finally:
        runner.quit()
    duration = time.monotonic() - started
    entries = environment.stats.entries
    flow = entries.get(("agent execution", "FLOW"))
    metrics = {
        "users": min(workload["concurrency"], count),
        "flows": count,
        "http_rps": sum(entry.num_requests for entry in entries.values() if entry.method != "FLOW") / duration,
        "execution_p95_ms": flow.get_response_time_percentile(0.95) if flow else 0,
    }
    return {"results": results, "metrics": metrics}

"""Run the fixed concurrency scenario with headless Locust users."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from evals.clients.execution_timing import completed_run_seconds
from evals.clients.load_client import TERMINAL, Result
from evals.scenarios.base import Scenario, map_task


def make_workload(scenario: Scenario) -> dict:
    if scenario.mode != "concurrency":
        raise ValueError("concurrency executor requires a concurrency scenario")
    config = scenario.inputs.config
    workload = {
        "host": config["agent_base_url"],
        "cases": [asdict(map_task(task, scenario.inputs.database)) for task in scenario.inputs.tasks],
        "concurrency": scenario.concurrency,
        "repeat": scenario.repeat,
        "run_mode": scenario.run_mode,
        "duration_seconds": scenario.duration_seconds,
        "request_timeout": float(config.get("request_timeout", 10)),
        "poll_interval": float(config.get("poll_interval", 1)),
        "wait_timeout": float(config.get("wait_timeout", 180)),
        "llm_backend": scenario.llm_backend,
        "mock_mode": scenario.mock_mode,
    }
    if min(workload[key] for key in ("request_timeout", "poll_interval", "wait_timeout")) <= 0:
        raise ValueError("request_timeout, poll_interval and wait_timeout must be positive")
    return workload


def run_timeout(workload: dict) -> float:
    if workload["run_mode"] == "duration":
        return workload["duration_seconds"] + workload["wait_timeout"] + 3 * workload["request_timeout"] + 30
    count = len(workload["cases"]) * workload["repeat"]
    batches = (count + workload["concurrency"] - 1) // workload["concurrency"]
    return batches * (workload["wait_timeout"] + 3 * workload["request_timeout"] + 10) + 30


def print_metrics(payload: dict) -> None:
    metrics = payload["metrics"]
    print(
        f"Locust: {metrics['users']} users, {metrics['flows']} executions, "
        f"{metrics['http_rps']:.2f} HTTP req/s, "
        f"execution p95 {metrics['execution_p95_ms']:.0f} ms"
    )


def execute(scenario: Scenario, token: str) -> list[Result]:
    """Run a finite, headless Locust workload and return per-task results."""
    workload = make_workload(scenario)
    with tempfile.TemporaryDirectory(prefix="resolveai-locust-") as directory:
        source = Path(directory) / "workload.json"
        destination = Path(directory) / "result.json"
        source.write_text(json.dumps(workload), encoding="utf-8")
        environment = os.environ.copy()
        environment["RESOLVEAI_LOAD_TOKEN"] = token
        completed = subprocess.run(
            [sys.executable, "-m", "evals.locust_worker", str(source), str(destination)],
            env=environment, capture_output=True, text=True, check=False, timeout=run_timeout(workload),
        )
        if completed.returncode:
            raise RuntimeError(f"Locust run failed: {completed.stderr.strip()[-2000:]}")
        payload = json.loads(destination.read_text(encoding="utf-8"))
    print_metrics(payload)
    return [Result(**row) for row in payload["results"]]


@contextmanager
def execute_with_web_ui(scenario: Scenario, token: str, port: int) -> Iterator[tuple[list[Result], subprocess.Popen]]:
    """Serve the exact runner workload in Locust's Web UI until the caller leaves."""
    workload = make_workload(scenario)
    with tempfile.TemporaryDirectory(prefix="resolveai-locust-") as directory:
        source = Path(directory) / "workload.json"
        destination = Path(directory) / "result.json"
        release = Path(directory) / "release"
        log_path = Path(directory) / "worker.log"
        source.write_text(json.dumps(workload), encoding="utf-8")
        environment = os.environ.copy()
        environment["RESOLVEAI_LOAD_TOKEN"] = token
        with log_path.open("w+") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "evals.locust_worker", str(source), str(destination), str(port), str(release)],
                env=environment, stdout=log, stderr=subprocess.STDOUT, text=True,
            )
            print(f"Locust Web UI: http://127.0.0.1:{port}", flush=True)
            try:
                deadline = time.monotonic() + run_timeout(workload)
                while not destination.exists():
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(f"Locust run failed: {log.read()[-2000:]}")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Locust run exceeded its workload timeout")
                    time.sleep(0.1)
                payload = json.loads(destination.read_text(encoding="utf-8"))
                print_metrics(payload)
                yield [Result(**row) for row in payload["results"]], process
            finally:
                release.touch()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def run_locust(workload: dict, token: str, *, web_port: int | None = None,
               on_complete: Callable[[dict], None] | None = None) -> dict:
    """Execute a finite workload; called after gevent patching in the worker."""
    import gevent
    from gevent.event import Event
    from gevent.queue import Empty, Queue
    from locust import HttpUser, constant, task
    from locust.env import Environment
    from locust.exception import StopUser

    duration_mode = workload["run_mode"] == "duration"
    jobs = Queue()
    if not duration_mode:
        for repetition in range(1, workload["repeat"] + 1):
            for case in workload["cases"]:
                jobs.put((jobs.qsize(), case, repetition))
    count = jobs.qsize()
    results: dict[int, dict] = {}
    finished = Event()
    remaining = count
    next_index = 0
    batch_id = uuid4().hex
    run_deadline = time.monotonic() + workload["duration_seconds"] if duration_mode else None

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
            nonlocal next_index, remaining
            if duration_mode:
                if time.monotonic() >= run_deadline:
                    raise StopUser()
                index = next_index
                next_index += 1
                case = workload["cases"][index % len(workload["cases"])]
                repetition = index // len(workload["cases"]) + 1
            else:
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
                terminal_response = submission
                result.submit_seconds = time.monotonic() - started
                poll_deadline = time.monotonic() + workload["wait_timeout"]
                while result.status not in TERMINAL:
                    if time.monotonic() >= poll_deadline:
                        result.status = "POLL_TIMEOUT"
                        break
                    gevent.sleep(min(workload["poll_interval"], max(0, poll_deadline - time.monotonic())))
                    execution = self.request_json(
                        "GET", f"/executions/{result.execution_id}", headers,
                        name="GET /executions/:id",
                    )
                    result.status = execution["status"]
                    result.error = execution.get("error_code")
                    terminal_response = execution
                if result.status in TERMINAL:
                    result.total_seconds = completed_run_seconds(terminal_response)
            except Exception as exc:  # noqa: BLE001 - continue other load jobs
                result.status = "ERROR"
                result.error = str(exc)
            finally:
                if result.total_seconds is None:
                    result.total_seconds = time.monotonic() - started
                self.environment.events.request.fire(
                    request_type="FLOW", name="agent execution",
                    response_time=result.total_seconds * 1000, response_length=0,
                    exception=None if result.status == "SUCCEEDED" else RuntimeError(result.error or result.status),
                    context={},
                )
                results[index] = asdict(result)
                if not duration_mode:
                    remaining -= 1
                    if remaining == 0:
                        finished.set()

    environment = Environment(user_classes=[AgentUser], host=workload["host"])
    runner = environment.create_local_runner()
    web_ui = environment.create_web_ui(host="127.0.0.1", port=web_port) if web_port is not None else None
    started = time.monotonic()
    try:
        users = workload["concurrency"] if duration_mode else min(workload["concurrency"], count)
        runner.start(users, spawn_rate=users)
        if duration_mode:
            runner.spawning_greenlet.join()
            runner.user_greenlets.join()
        else:
            finished.wait()
        runner.stop()
        duration = time.monotonic() - started
        entries = environment.stats.entries
        flow = entries.get(("agent execution", "FLOW"))
        metrics = {
            "users": users,
            "flows": len(results),
            "http_rps": sum(entry.num_requests for entry in entries.values() if entry.method != "FLOW") / duration,
            "execution_p95_ms": flow.get_response_time_percentile(0.95) if flow else 0,
        }
        payload = {"results": [results[index] for index in sorted(results)], "metrics": metrics}
        if on_complete is not None:
            on_complete(payload)
        return payload
    finally:
        if web_ui is not None:
            web_ui.stop()
        runner.quit()

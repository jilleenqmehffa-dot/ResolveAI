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
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import SQLAlchemyError

from evals.clients.execution_timing import completed_run_seconds
from evals.clients.load_client import TERMINAL, Result
from evals.scenarios.base import Scenario, map_task
from services.agent_service.app.models.agent import AgentRun


def make_workload(scenario: Scenario) -> dict:
    if scenario.mode != "concurrency":
        raise ValueError("concurrency executor requires a concurrency scenario")
    config = scenario.inputs.config
    workload = {
        "host": config["agent_base_url"],
        "cases": [asdict(map_task(task, scenario.inputs.customer_oracle)) for task in scenario.inputs.tasks],
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
    p95 = metrics["execution_p95_ms"]
    p95_text = f"{p95:.0f} ms" if p95 is not None else "n/a"
    tps = metrics["completed_tasks_per_second"]
    tps_text = f"{tps:.2f}" if tps is not None else "n/a"
    print(
        f"Locust: {metrics['users']} users, {metrics['flows']} executions, "
        f"{metrics['completed_tasks']} completed, "
        f"{tps_text} completed tasks/s, "
        f"{metrics['successful_tasks']} succeeded, "
        f"success rate {metrics['success_rate']:.1%}, "
        f"E2E p95 {p95_text}"
    )


def web_summary_html(metrics: dict | None) -> str:
    if metrics is None:
        refresh = '<meta http-equiv="refresh" content="2">'
        body = "<p>压测提交或结果收集进行中；完成后本页会自动刷新。</p>"
    else:
        refresh = ""
        tps = metrics["completed_tasks_per_second"]
        p95 = metrics["execution_p95_ms"]
        values = (
            ("尝试任务", str(metrics["flows"])),
            ("完成任务", str(metrics["completed_tasks"])),
            ("完成 TPS", f"{tps:.2f}" if tps is not None else "n/a"),
            ("成功任务", str(metrics["successful_tasks"])),
            ("成功率", f"{metrics['success_rate']:.1%}"),
            ("E2E P95", f"{p95:.0f} ms" if p95 is not None else "n/a"),
        )
        body = "<dl>" + "".join(
            f"<div><dt>{label}</dt><dd>{value}</dd></div>" for label, value in values
        ) + "</dl>"
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"{refresh}<title>ResolveAI 压测结果</title>"
        "<style>body{font:16px system-ui,sans-serif;max-width:860px;margin:4rem auto;padding:0 1rem;}"
        "dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;}"
        "dl div{padding:1rem;border:1px solid #bbb;border-radius:8px;}"
        "dt{color:#555;}dd{margin:.5rem 0 0;font-size:1.6rem;font-weight:700;}"
        "a{color:#1463c3;}</style></head><body><a href='/'>返回 Locust 请求统计</a>"
        f"<h1>ResolveAI 压测结果</h1>{body}</body></html>"
    )


def check_agent_database(database_url: str) -> None:
    """Fail before submitting load if AgentRun results cannot be read."""
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM agent_runs LIMIT 1"))
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "Cannot read Agent database before load; check AGENT_DATABASE_URL "
            "and the PostgreSQL host port mapping"
        ) from exc
    finally:
        engine.dispose()


def collect_run_results(results: dict[int, dict], database_url: str, *,
                        interval: float, timeout: float) -> tuple[datetime | None, datetime | None]:
    """Read submitted runs from Agent DB only after HTTP load has stopped."""
    pending = {
        UUID(row["execution_id"]): row
        for row in results.values() if row["execution_id"] is not None
    }
    if not pending:
        return None, None
    engine = create_engine(database_url, pool_pre_ping=True)
    deadline = time.monotonic() + timeout
    first_created_at = None
    last_finished_at = None
    try:
        while pending:
            with engine.connect() as connection:
                identifiers = list(pending)
                for offset in range(0, len(identifiers), 500):
                    batch = identifiers[offset:offset + 500]
                    rows = connection.execute(
                        select(
                            AgentRun.id, AgentRun.status, AgentRun.error_code,
                            AgentRun.created_at, AgentRun.finished_at,
                        ).where(AgentRun.id.in_(batch))
                    )
                    for run_id, status, error_code, created_at, finished_at in rows:
                        if first_created_at is None or created_at < first_created_at:
                            first_created_at = created_at
                        if status.value not in TERMINAL:
                            continue
                        result = pending.pop(run_id, None)
                        if result is None:
                            continue
                        result["status"] = status.value
                        result["error"] = error_code
                        if finished_at is None:
                            raise ValueError(f"terminal AgentRun {run_id} has no finished_at")
                        if last_finished_at is None or finished_at > last_finished_at:
                            last_finished_at = finished_at
                        result["total_seconds"] = completed_run_seconds({
                            "created_at": created_at.isoformat(),
                            "updated_at": finished_at.isoformat(),
                        })
            if not pending or time.monotonic() >= deadline:
                break
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
    finally:
        engine.dispose()
    for result in pending.values():
        result["status"] = "COMPLETION_TIMEOUT"
        result["error"] = "AgentRun did not reach a terminal state before the completion deadline"
    return first_created_at, last_finished_at


def execute(scenario: Scenario, token: str) -> list[Result]:
    """Run a finite, headless Locust workload and return per-task results."""
    workload = make_workload(scenario)
    database_url = str(scenario.setting("AGENT_DATABASE_URL"))
    check_agent_database(database_url)
    with tempfile.TemporaryDirectory(prefix="resolveai-locust-") as directory:
        source = Path(directory) / "workload.json"
        destination = Path(directory) / "result.json"
        source.write_text(json.dumps(workload), encoding="utf-8")
        environment = os.environ.copy()
        environment["RESOLVEAI_LOAD_TOKEN"] = token
        environment["RESOLVEAI_LOAD_AGENT_DATABASE_URL"] = database_url
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
    database_url = str(scenario.setting("AGENT_DATABASE_URL"))
    check_agent_database(database_url)
    with tempfile.TemporaryDirectory(prefix="resolveai-locust-") as directory:
        source = Path(directory) / "workload.json"
        destination = Path(directory) / "result.json"
        release = Path(directory) / "release"
        log_path = Path(directory) / "worker.log"
        source.write_text(json.dumps(workload), encoding="utf-8")
        environment = os.environ.copy()
        environment["RESOLVEAI_LOAD_TOKEN"] = token
        environment["RESOLVEAI_LOAD_AGENT_DATABASE_URL"] = database_url
        with log_path.open("w+") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "evals.locust_worker", str(source), str(destination), str(port), str(release)],
                env=environment, stdout=log, stderr=subprocess.STDOUT, text=True,
            )
            print(f"Locust Web UI: http://127.0.0.1:{port}", flush=True)
            print(f"任务完成指标: http://127.0.0.1:{port}/resolveai/results", flush=True)
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
            headers = {"X-Agent-Token": token, "X-Customer-ID": f"eval-anon:{uuid4().hex}"}
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
            except Exception as exc:  # noqa: BLE001 - continue other load jobs
                result.status = "ERROR"
                result.error = str(exc)
            finally:
                if result.status == "ERROR":
                    result.total_seconds = time.monotonic() - started
                results[index] = asdict(result)
                if not duration_mode:
                    remaining -= 1
                    if remaining == 0:
                        finished.set()

    environment = Environment(user_classes=[AgentUser], host=workload["host"])
    runner = environment.create_local_runner()
    web_ui = environment.create_web_ui(host="127.0.0.1", port=web_port) if web_port is not None else None
    summary_state: dict[str, dict | None] = {"metrics": None}
    if web_ui is not None:
        web_ui.app.add_url_rule(
            "/resolveai/results", "resolveai_results",
            lambda: web_summary_html(summary_state["metrics"]),
        )
    try:
        users = workload["concurrency"] if duration_mode else min(workload["concurrency"], count)
        runner.start(users, spawn_rate=users)
        if duration_mode:
            runner.spawning_greenlet.join()
            runner.user_greenlets.join()
        else:
            finished.wait()
        runner.stop()
        first_created_at, last_finished_at = collect_run_results(
            results, os.environ["RESOLVEAI_LOAD_AGENT_DATABASE_URL"],
            interval=workload["poll_interval"], timeout=workload["wait_timeout"],
        )
        for row in results.values():
            if row["total_seconds"] is not None:
                flow_error = (
                    None if row["status"] == "SUCCEEDED"
                    else RuntimeError(row["error"] or row["status"])
                )
                environment.events.request.fire(
                    request_type="FLOW", name="agent execution",
                    response_time=row["total_seconds"] * 1000,
                    response_length=0, exception=flow_error, context={},
                )
        completed_times = sorted(
            row["total_seconds"] for row in results.values()
            if row["status"] in TERMINAL and row["total_seconds"] is not None
        )
        completed_tasks = len(completed_times)
        successful_tasks = sum(row["status"] == "SUCCEEDED" for row in results.values())
        completion_span = (
            (last_finished_at - first_created_at).total_seconds()
            if first_created_at is not None and last_finished_at is not None else 0
        )
        # Nearest-rank p95 over server-side completion times.
        p95_index = (95 * completed_tasks + 99) // 100 - 1
        metrics = {
            "users": users,
            "flows": len(results),
            "completed_tasks": completed_tasks,
            "completed_tasks_per_second": completed_tasks / completion_span if completion_span > 0 else None,
            "successful_tasks": successful_tasks,
            "success_rate": successful_tasks / len(results) if results else 0,
            "execution_p95_ms": completed_times[p95_index] * 1000 if completed_times else None,
        }
        summary_state["metrics"] = metrics
        payload = {"results": [results[index] for index in sorted(results)], "metrics": metrics}
        if on_complete is not None:
            on_complete(payload)
        return payload
    finally:
        if web_ui is not None:
            web_ui.stop()
        runner.quit()

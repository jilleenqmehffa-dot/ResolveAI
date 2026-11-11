"""Run Bench tasks through the existing Agent, adapters, and evaluators."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import yaml
from dotenv import load_dotenv

from evals.adapter.commerce_adapter import CommerceAdapter
from evals.adapter.trace_adapter import TraceAdapter
from evals.clients.benchmark_client import BenchCase, _customer_id
from evals.clients.load_client import submit_case
from evals.evaluation.task_success import TaskSuccessEvaluator
from evals.evaluation.trajectory import TrajectoryEvaluator
from evals.normalizer.commerce_normalizer import CommerceNormalizer
from evals.normalizer.models import EvalRecord
from evals.normalizer.trace_normalizer import TraceNormalizer


def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a nonempty JSON array")
    if any(not isinstance(task, dict) or "id" not in task for task in tasks):
        raise ValueError("every task must be an object with an id")
    return tasks


def select_split(
    tasks: list[dict[str, Any]], split_path: Path, split: str
) -> list[dict[str, Any]]:
    splits = json.loads(split_path.read_text(encoding="utf-8"))
    if split not in splits or not isinstance(splits[split], list):
        raise ValueError(f"split {split!r} is missing or invalid in {split_path}")
    by_id = {str(task["id"]): task for task in tasks}
    selected = []
    for task_id in splits[split]:
        if str(task_id) not in by_id:
            raise ValueError(f"task {task_id!r} from split {split!r} is missing")
        selected.append(by_id[str(task_id)])
    return selected


def map_task(task: dict[str, Any], database: dict[str, Any]) -> BenchCase:
    if "customer_id" in task and "message" in task:
        return BenchCase(str(task["id"]), str(task["customer_id"]), str(task["message"]))
    instructions = task["user_scenario"]["instructions"]
    known = instructions["known_info"].strip()
    reason = instructions["reason_for_call"].strip()
    customer_id = _customer_id(known, reason, database["users"], database["orders"])
    return BenchCase(str(task["id"]), customer_id, f"{known}\n\n{reason}")


def reasons(success: Any, trajectory: Any) -> list[str]:
    details = [check.detail for check in (*success.db_checks, *success.nl_checks) if not check.passed]
    details.extend(
        f"missing action {match.action_id}" for match in trajectory.matches
        if match.matched_run_id is None
    )
    details.extend(f"unexpected write {run_id}" for run_id in trajectory.extra_write_run_ids)
    details.extend(f"unsupported action {action_id}" for action_id in trajectory.unsupported_actions)
    return details or ["all evaluated checks passed"]


async def run_task(
    task: dict[str, Any], database: dict[str, Any], config: dict[str, Any],
    client: httpx.AsyncClient, token: str, batch_id: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {"task_id": str(task["id"]), "customer_id": None,
                           "execution_id": None, "status": "ERROR", "scores": None,
                           "reasons": [], "error": None}
    try:
        case = map_task(task, database)
        if not case.customer_id.strip() or not case.message.strip():
            raise ValueError("customer_id and message must be nonempty")
        row["customer_id"] = case.customer_id
        async with CommerceAdapter(
            case.customer_id, base_url=config["commerce_base_url"], agent_token=token
        ) as commerce:
            before = await commerce.capture_task(task)
            submitted = await submit_case(
                client, case, 1, batch_id, token, True,
                float(config.get("poll_interval", 1)),
                float(config.get("wait_timeout", 180)),
            )
            row["execution_id"] = submitted.execution_id
            row["status"] = submitted.status
            if submitted.status != "SUCCEEDED":
                raise RuntimeError(submitted.error or f"execution ended: {submitted.status}")
            after = await commerce.capture_task(task)
        traces = await asyncio.to_thread(
            TraceAdapter(project=config.get("langsmith_project")).wait_for_traces,
            submitted.execution_id, timeout=float(config.get("trace_timeout", 30)),
        )
        if not traces:
            raise RuntimeError("no LangSmith trace found")
        normalizer = CommerceNormalizer()
        record = EvalRecord(
            task_id=case.task_id, customer_id=case.customer_id,
            execution_id=submitted.execution_id,
            trace=TraceNormalizer().normalize(traces),
            commerce=normalizer.normalize(after),
            commerce_before=normalizer.normalize(before),
        )
        success = TaskSuccessEvaluator().evaluate(record, task)
        trajectory = TrajectoryEvaluator().evaluate(record, task)
        row["scores"] = {"task_success": success.score, "trajectory": trajectory.score}
        row["reasons"] = reasons(success, trajectory)
        row["details"] = {"task_success": asdict(success), "trajectory": asdict(trajectory)}
    except Exception as exc:  # noqa: BLE001 - one failed task must not discard the batch
        row["error"] = {"type": type(exc).__name__, "message": str(exc)}
        row["reasons"] = [str(exc)]
    return row


async def run(tasks: list[dict[str, Any]], database: dict[str, Any], config: dict[str, Any], token: str) -> list[dict[str, Any]]:
    concurrency = int(config.get("concurrency", 1))
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        base_url=config["agent_base_url"], timeout=float(config.get("request_timeout", 10)),
        limits=httpx.Limits(max_connections=concurrency),
    ) as client:
        batch_id = uuid4().hex
        completed = 0

        async def bounded(task: dict[str, Any]) -> dict[str, Any]:
            nonlocal completed
            async with semaphore:
                row = await run_task(task, database, config, client, token, batch_id)
                completed += 1
                outcome = (
                    f"error: {row['error']['type']}"
                    if row["error"] else f"scored: {row['scores']}"
                )
                print(f"[{completed}/{len(tasks)}] task {row['task_id']}: {outcome}", flush=True)
                return row

        return await asyncio.gather(*(bounded(task) for task in tasks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, help="JSON task array; overrides config tasks and split")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--task-id", help="evaluate only this task ID, regardless of config split")
    selection.add_argument("--all", action="store_true", help="evaluate every task in the configured split")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        parser.error("config must be a YAML mapping")
    load_dotenv(config.get("env_file", ".env"))
    source = args.tasks or config.get("tasks")
    if not source:
        parser.error("specify --tasks or tasks in config")
    task_path = Path(source)
    database_path = Path(config.get("database", "evals/datasets/db.json"))
    token = os.getenv("AGENT_INTERNAL_TOKEN")
    if not token:
        parser.error("AGENT_INTERNAL_TOKEN is required")
    for key in ("agent_base_url", "commerce_base_url"):
        if not config.get(key):
            parser.error(f"{key} is required in config")
    tasks = load_tasks(task_path)
    if args.task_id is not None:
        tasks = [task for task in tasks if str(task["id"]) == args.task_id]
        if len(tasks) != 1:
            parser.error(f"task ID {args.task_id!r} must match exactly one task in {task_path}")
    elif args.tasks is None and config.get("split"):
        if not config.get("split_file"):
            parser.error("split_file is required when split is configured")
        tasks = select_split(tasks, Path(config["split_file"]), str(config["split"]))
    database = json.loads(database_path.read_text(encoding="utf-8"))
    rows = asyncio.run(run(tasks, database, config, token))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    scored = [row for row in rows if row["scores"] is not None]
    print(
        f"Wrote {len(rows)} results to {args.output}; "
        f"scored: {len(scored)}; errors: {sum(bool(row['error']) for row in rows)}"
    )
    if scored:
        print(
            "Mean scores: "
            + ", ".join(
                f"{name}={sum(row['scores'][name] for row in scored) / len(scored):.3f}"
                for name in ("task_success", "trajectory")
            )
        )
    return 1 if any(row["error"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())

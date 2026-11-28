"""Shared dataset selection and environment contract for evaluation scenarios."""

from __future__ import annotations

import json
import os
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from data_platform.core import manifest, source_path, validate_sources
from evals.clients.benchmark_client import BenchCase


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


def map_task(task: dict[str, Any], customer_oracle: dict[str, str]) -> BenchCase:
    if "customer_id" in task and "message" in task:
        return BenchCase(str(task["id"]), str(task["customer_id"]), str(task["message"]))
    instructions = task["user_scenario"]["instructions"]
    known = instructions["known_info"].strip()
    reason = instructions["reason_for_call"].strip()
    customer_id = resolve_customer_id(task, customer_oracle)
    return BenchCase(str(task["id"]), customer_id, f"{known}\n\n{reason}")


def resolve_customer_id(task: dict[str, Any], customer_oracle: dict[str, str]) -> str:
    """Read private evaluator metadata; never infer identity from scenario prose."""
    task_id = str(task["id"])
    customer_id = customer_oracle.get(task_id)
    if not customer_id:
        raise ValueError(f"task {task_id}: missing private customer oracle")
    return customer_id


@dataclass(frozen=True)
class LoadedInputs:
    config_path: Path
    config: dict[str, Any]
    task_path: Path
    database_path: Path
    tasks: list[dict[str, Any]]
    database: dict[str, Any]
    customer_oracle: dict[str, str]
    split: str | None
    dataset_version: str | None


def load_inputs(args: Namespace, default_config: Path) -> LoadedInputs:
    config_path = args.config or default_config
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise TypeError("config must be a YAML mapping")
    source = args.tasks or config.get("tasks")
    if not source:
        raise ValueError("specify --tasks or tasks in config")
    task_path = Path(source)
    database_path = Path(config.get("database", "evals/scenarios/agent_task/datasets/db.json"))
    pinned = (
        task_path.resolve() == source_path("tasks").resolve()
        and database_path.resolve() == source_path("retail").resolve()
        and Path(config.get("split_file", "")).resolve() == source_path("splits").resolve()
    )
    if pinned:
        validate_sources()
    tasks = load_tasks(task_path)
    split = None
    if args.task_id is not None:
        tasks = [task for task in tasks if str(task["id"]) == args.task_id]
        if len(tasks) != 1:
            raise ValueError(f"task ID {args.task_id!r} must match exactly one task in {task_path}")
    elif args.tasks is None and config.get("split"):
        if not config.get("split_file"):
            raise ValueError("split_file is required when split is configured")
        split = str(config["split"])
        tasks = select_split(tasks, Path(config["split_file"]), split)
    if args.limit is not None:
        tasks = tasks[:args.limit]
    if not tasks:
        raise ValueError("no tasks selected")
    database = json.loads(database_path.read_text(encoding="utf-8"))
    oracle_source = config.get("customer_oracle")
    if oracle_source is None and task_path.resolve() == source_path("tasks").resolve():
        oracle_source = source_path("task_customers")
    if oracle_source is None and args.mode == "agent-task":
        raise ValueError("custom agent tasks require customer_oracle in the scenario config")
    customer_oracle = (
        json.loads(Path(oracle_source).read_text(encoding="utf-8"))
        if oracle_source is not None else {}
    )
    if not isinstance(customer_oracle, dict):
        raise TypeError("customer oracle must be a JSON object")
    if args.mode == "agent-task":
        for task in tasks:
            customer_id = resolve_customer_id(task, customer_oracle)
            if customer_id not in database["users"]:
                raise ValueError(f"task {task['id']}: customer oracle ID is absent from database")
    return LoadedInputs(
        config_path, config, task_path, database_path, tasks, database, customer_oracle, split,
        manifest()["version"] if pinned else None,
    )


@dataclass(frozen=True)
class Scenario:
    mode: str
    inputs: LoadedInputs
    llm_backend: str
    concurrency: int
    repeat: int
    required_services: tuple[str, ...]
    mock_mode: str = "fast"
    fault: str | None = None
    fault_ms: int | None = None
    toxiproxy_api: str | None = None
    rebuild_each_task: bool = False
    test_database_url: str | None = None
    run_mode: str = "count"
    duration_seconds: float | None = None
    fault_target: str = "commerce"
    fault_case: str | None = None
    outage_seconds: float | None = None

    @property
    def required_client_env(self) -> tuple[str, ...]:
        if self.mode == "agent-task":
            required = ("AGENT_INTERNAL_TOKEN", "LANGSMITH_API_KEY", "USER_SIMULATOR_API_KEY")
            return required + (("AGENT_DATABASE_URL",) if self.fault else ())
        if self.mode == "concurrency":
            return ("AGENT_INTERNAL_TOKEN", "AGENT_DATABASE_URL")
        return ("AGENT_INTERNAL_TOKEN", "AGENT_DATABASE_URL") if self.fault_case else ("AGENT_INTERNAL_TOKEN",)

    @property
    def required_worker_env(self) -> tuple[str, ...]:
        required = ("AGENT_INTERNAL_TOKEN", "LANGSMITH_API_KEY") if self.mode == "agent-task" else ("AGENT_INTERNAL_TOKEN",)
        return required + (("DEEPSEEK_API_KEY",) if self.llm_backend == "deepseek" else ())

    def setting(self, name: str) -> str | None:
        env_file = self.inputs.config.get("env_file", ".env")
        return os.getenv(name) or dotenv_values(env_file).get(name)

    def require_client_settings(self) -> str:
        missing = [name for name in self.required_client_env if not self.setting(name)]
        if missing:
            raise ValueError("missing client settings: " + ", ".join(missing))
        return str(self.setting("AGENT_INTERNAL_TOKEN"))

    def preview(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "config": str(self.inputs.config_path),
            "dataset_version": self.inputs.dataset_version,
            "split": self.inputs.split,
            "task_ids": [str(task["id"]) for task in self.inputs.tasks],
            "planned_requests": (
                None if self.mode == "concurrency" and self.run_mode == "duration"
                else len(self.inputs.tasks) * self.repeat
            ),
            "run_mode": self.run_mode if self.mode == "concurrency" else None,
            "duration_seconds": self.duration_seconds,
            "llm_backend": self.llm_backend,
            "mock_mode": self.mock_mode,
            "concurrency": self.concurrency,
            "repeat": None if self.mode == "concurrency" and self.run_mode == "duration" else self.repeat,
            "fault": self.fault,
            "fault_target": self.fault_target,
            "fault_ms": self.fault_ms,
            "fault_case": self.fault_case,
            "outage_seconds": self.outage_seconds,
            "toxiproxy_api": self.toxiproxy_api,
            "required_services": list(self.required_services),
            "required_client_env": list(self.required_client_env),
            "required_worker_env": list(self.required_worker_env),
        }


def make_scenario(
    mode: str, inputs: LoadedInputs, *, llm_backend: str, concurrency: int,
    repeat: int, required_services: tuple[str, ...], fault: str | None = None,
    fault_ms: int | None = None, toxiproxy_api: str | None = None,
    rebuild_each_task: bool = False,
    test_database_url: str | None = None,
    mock_mode: str = "fast",
    run_mode: str = "count",
    duration_seconds: float | None = None,
    fault_target: str = "commerce",
    fault_case: str | None = None,
    outage_seconds: float | None = None,
) -> Scenario:
    if llm_backend not in {"deepseek", "mock"}:
        raise ValueError("llm_backend must be deepseek or mock")
    if mock_mode not in {"fast", "latency"}:
        raise ValueError("mock_mode must be fast or latency")
    if concurrency < 1 or repeat < 1:
        raise ValueError("concurrency and repeat must be positive")
    if not inputs.config.get("agent_base_url"):
        raise ValueError("agent_base_url is required in config")
    if mode == "agent-task" and not inputs.config.get("commerce_base_url"):
        raise ValueError("commerce_base_url is required in config")
    return Scenario(
        mode, inputs, llm_backend, concurrency, repeat, required_services,
        mock_mode, fault, fault_ms, toxiproxy_api, rebuild_each_task, test_database_url,
        run_mode, duration_seconds, fault_target, fault_case, outage_seconds,
    )

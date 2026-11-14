"""Agent task dataset, scoring dependencies, and reset requirements."""

import os
from argparse import Namespace
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from data_platform.core import source_path
from evals.scenarios.base import Scenario, load_inputs, make_scenario

DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")
SERVICES = ("agent-api", "agent-worker", "commerce", "postgres", "rabbitmq")


def prepare(args: Namespace) -> Scenario:
    if args.mock_mode is not None:
        raise ValueError("--mock-mode is only valid for concurrency mode")
    inputs = load_inputs(args, DEFAULT_CONFIG)
    config = inputs.config
    if args.fault is not None or args.fault_ms is not None or args.toxiproxy_api:
        raise ValueError("fault options are only valid for network-fault mode")
    repeat = args.repeat if args.repeat is not None else int(config.get("repeat", 1))
    if repeat != 1:
        raise ValueError("agent-task mode does not repeat tasks")
    concurrency = args.concurrency or int(config.get("concurrency", 1))
    rebuild = args.rebuild_each_task or bool(config.get("rebuild_each_task", False))
    database_url = None
    if rebuild:
        if inputs.task_path.resolve() != source_path("tasks").resolve() or inputs.database_path.resolve() != source_path("retail").resolve():
            raise ValueError("rebuilding requires the pinned tasks and retail dataset")
        if concurrency != 1:
            raise ValueError("rebuilding before each task requires concurrency=1")
        variable = str(config.get("test_database_env", "DATA_PLATFORM_TEST_DATABASE_URL"))
        database_url = os.getenv(variable) or dotenv_values(config.get("env_file", ".env")).get(variable)
        if not database_url or not args.confirm_database or make_url(database_url).database != args.confirm_database:
            raise ValueError(f"set {variable} and confirm its exact database name")
    elif args.confirm_database:
        raise ValueError("--confirm-database requires --rebuild-each-task")
    return make_scenario(
        "agent-task", inputs,
        llm_backend=args.llm_backend or str(config.get("llm_backend", "deepseek")),
        concurrency=concurrency, repeat=repeat, required_services=SERVICES,
        rebuild_each_task=rebuild, test_database_url=database_url,
    )

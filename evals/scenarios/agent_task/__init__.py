"""Agent task dataset, scoring dependencies, and reset requirements."""

import os
from argparse import Namespace
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from data_platform.core import source_path
from evals.scenarios.base import Scenario, load_inputs, make_scenario
from infra.toxiproxy.control import FAULTS, TARGETS

DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")
SERVICES = ("agent-api", "agent-worker", "commerce", "postgres", "rabbitmq")


def prepare(args: Namespace) -> Scenario:
    if args.mock_mode is not None:
        raise ValueError("--mock-mode is only valid for concurrency mode")
    if args.fault_case is not None or args.outage_seconds is not None:
        raise ValueError("named fault cases are only valid for network-fault mode")
    inputs = load_inputs(args, DEFAULT_CONFIG)
    config = inputs.config
    fault = args.fault or config.get("fault")
    if fault is None and any(
        value is not None for value in (args.fault_target, args.fault_ms, args.toxiproxy_api)
    ):
        raise ValueError("--fault is required with other fault options")
    if fault is not None and fault not in FAULTS:
        raise ValueError(f"agent-task fault must be one of: {', '.join(FAULTS)}")
    fault_target = args.fault_target or str(config.get("fault_target", "commerce"))
    if fault_target not in TARGETS:
        raise ValueError(f"agent-task fault target must be one of: {', '.join(TARGETS)}")
    fault_ms = args.fault_ms if args.fault_ms is not None else int(config.get("fault_ms", 1000))
    if fault_ms < 0:
        raise ValueError("fault_ms must be nonnegative")
    toxiproxy_api = args.toxiproxy_api or str(config.get("toxiproxy_api", "http://127.0.0.1:8474"))
    repeat = args.repeat if args.repeat is not None else int(config.get("repeat", 1))
    if repeat != 1:
        raise ValueError("agent-task mode does not repeat tasks")
    concurrency = args.concurrency or int(config.get("concurrency", 1))
    if concurrency != 1:
        raise ValueError("agent-task database restoration requires concurrency=1")
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
        concurrency=concurrency, repeat=repeat,
        required_services=SERVICES + (("toxiproxy",) if fault else ()),
        rebuild_each_task=rebuild, test_database_url=database_url,
        fault=fault, fault_target=fault_target, fault_ms=fault_ms if fault else None,
        toxiproxy_api=toxiproxy_api if fault else None,
    )

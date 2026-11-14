"""Concurrent submission dataset and load settings."""

from argparse import Namespace
from pathlib import Path

from evals.scenarios.base import Scenario, load_inputs, make_scenario

DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")
SERVICES = ("agent-api", "agent-worker", "commerce", "postgres", "rabbitmq")


def prepare(args: Namespace) -> Scenario:
    if args.fault is not None or args.fault_ms is not None or args.toxiproxy_api:
        raise ValueError("fault options are only valid for network-fault mode")
    if args.rebuild_each_task or args.confirm_database:
        raise ValueError("database rebuild is only valid for agent-task mode")
    inputs = load_inputs(args, DEFAULT_CONFIG)
    config = inputs.config
    backend = args.llm_backend or str(config.get("llm_backend", "mock"))
    mock_mode = args.mock_mode or str(config.get("mock_mode", "fast"))
    if backend != "mock" and mock_mode != "fast":
        raise ValueError("latency mock mode requires --llm-backend mock")
    return make_scenario(
        "concurrency", inputs,
        llm_backend=backend,
        mock_mode=mock_mode,
        concurrency=args.concurrency or int(config.get("concurrency", 10)),
        repeat=args.repeat if args.repeat is not None else int(config.get("repeat", 1)),
        required_services=SERVICES,
    )

"""Dataset, fault, and prerequisite settings for network evaluations."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from evals.scenarios.base import Scenario, load_inputs, make_scenario
from infra.toxiproxy.control import FAULTS

DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")
SERVICES = ("agent-api", "agent-worker", "commerce", "postgres", "rabbitmq", "toxiproxy")


def prepare(args: Namespace) -> Scenario:
    if args.mock_mode is not None:
        raise ValueError("--mock-mode is only valid for concurrency mode")
    if args.rebuild_each_task or args.confirm_database:
        raise ValueError("database rebuild is only valid for agent-task mode")
    inputs = load_inputs(args, DEFAULT_CONFIG)
    config = inputs.config
    fault = args.fault or config.get("fault")
    if fault not in FAULTS:
        raise ValueError(f"network-fault mode requires one of: {', '.join(FAULTS)}")
    fault_ms = args.fault_ms if args.fault_ms is not None else int(config.get("fault_ms", 1000))
    if fault_ms < 0:
        raise ValueError("fault_ms must be nonnegative")
    proxy_api = args.toxiproxy_api or config.get("toxiproxy_api")
    if not proxy_api:
        raise ValueError("toxiproxy_api is required in network-fault config")
    return make_scenario(
        "network-fault", inputs,
        llm_backend=args.llm_backend or str(config.get("llm_backend", "mock")),
        concurrency=args.concurrency or int(config.get("concurrency", 1)),
        repeat=args.repeat if args.repeat is not None else int(config.get("repeat", 1)),
        required_services=SERVICES, fault=fault, fault_ms=fault_ms,
        toxiproxy_api=str(proxy_api),
    )

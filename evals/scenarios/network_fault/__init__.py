"""Dataset, fault, and prerequisite settings for network evaluations."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from evals.scenarios.base import Scenario, load_inputs, make_scenario
from evals.scenarios.network_fault.cases import CASES
from infra.toxiproxy.control import FAULTS, TARGETS

DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")
SERVICES = ("agent-api", "agent-worker", "commerce", "postgres", "rabbitmq", "toxiproxy")


def prepare(args: Namespace) -> Scenario:
    if args.mock_mode is not None and args.fault_case is None:
        raise ValueError("--mock-mode is only valid for concurrency or named fault cases")
    if args.rebuild_each_task or args.confirm_database:
        raise ValueError("database rebuild is only valid for agent-task mode")
    inputs = load_inputs(args, DEFAULT_CONFIG)
    config = inputs.config
    fault_case = args.fault_case or config.get("fault_case")
    if fault_case is not None:
        if fault_case not in CASES:
            raise ValueError(f"unknown fault case: {fault_case}")
        if args.fault or args.fault_target or args.fault_ms is not None:
            raise ValueError("named fault cases set their own fault and target")
        if len(inputs.tasks) != 1:
            raise ValueError("named fault cases require exactly one selected task")
        if args.concurrency not in (None, 1) or args.repeat not in (None, 1):
            raise ValueError("named fault cases require concurrency=1 and repeat=1")
        recipe = CASES[fault_case]
        outage = args.outage_seconds if args.outage_seconds is not None else float(config.get("outage_seconds", 5))
        if not 0 < outage < float("inf"):
            raise ValueError("outage_seconds must be positive and finite")
        return make_scenario(
            "network-fault", inputs,
            llm_backend=args.llm_backend or "mock",
            mock_mode=args.mock_mode or recipe.mock_mode,
            concurrency=1, repeat=1,
            required_services=SERVICES,
            fault=recipe.fault, fault_target=recipe.target,
            fault_ms=0 if recipe.fault else None,
            toxiproxy_api=str(args.toxiproxy_api or config.get("toxiproxy_api", "http://127.0.0.1:8474")),
            fault_case=fault_case, outage_seconds=outage,
        )
    if args.outage_seconds is not None:
        raise ValueError("--outage-seconds requires --fault-case")
    fault = args.fault or config.get("fault")
    if fault not in FAULTS:
        raise ValueError(f"network-fault mode requires one of: {', '.join(FAULTS)}")
    target = args.fault_target or config.get("fault_target", "commerce")
    if target not in TARGETS:
        raise ValueError(f"network-fault mode requires a target from: {', '.join(TARGETS)}")
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
        toxiproxy_api=str(proxy_api), fault_target=target,
    )

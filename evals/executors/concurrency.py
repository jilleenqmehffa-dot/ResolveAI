"""Submit the configured load and collect each request's raw status and timing."""

from __future__ import annotations

import asyncio
from argparse import Namespace

from evals.clients.load_client import Result
from evals.clients.load_client import run as run_load
from evals.scenarios.base import Scenario, map_task


def execute(scenario: Scenario, token: str) -> list[Result]:
    if scenario.mode != "concurrency":
        raise ValueError("concurrency executor requires a concurrency scenario")
    config = scenario.inputs.config
    cases = [map_task(task, scenario.inputs.database) for task in scenario.inputs.tasks]
    options = Namespace(
        base_url=config["agent_base_url"],
        timeout=float(config.get("request_timeout", 10)),
        concurrency=scenario.concurrency,
        repeat=scenario.repeat,
        wait=True,
        poll_interval=float(config.get("poll_interval", 1)),
        wait_timeout=float(config.get("wait_timeout", 180)),
        llm_backend=scenario.llm_backend,
        mock_mode=scenario.mock_mode,
    )
    return asyncio.run(run_load(options, cases, token))

"""Prepare evaluation inputs and environment settings by scenario."""

from __future__ import annotations

from argparse import Namespace

from evals.scenarios.agent_task import prepare as prepare_agent_task
from evals.scenarios.base import Scenario
from evals.scenarios.concurrency import prepare as prepare_concurrency
from evals.scenarios.network_fault import prepare as prepare_network_fault


def prepare_scenario(args: Namespace) -> Scenario:
    preparers = {
        "agent-task": prepare_agent_task,
        "concurrency": prepare_concurrency,
        "network-fault": prepare_network_fault,
    }
    return preparers[args.mode](args)

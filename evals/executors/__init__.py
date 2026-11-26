"""Dispatch a prepared scenario to its execution module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from evals.clients.load_client import Result
from evals.executors.concurrency import execute as execute_concurrency
from evals.executors.network_fault import execute as execute_network_fault
from evals.executors.user_simulate.evaluation_launcher import (
    launch as launch_evaluation,
)
from evals.scenarios.base import Scenario

if TYPE_CHECKING:
    from evals.evaluators.agent_task import AgentExecution


def execute(scenario: Scenario, token: str) -> list[AgentExecution] | list[Result]:
    executors = {
        "agent-task": launch_evaluation,
        "concurrency": execute_concurrency,
        "network-fault": execute_network_fault,
    }
    return executors[scenario.mode](scenario, token)

"""Dispatch a prepared scenario to its execution module."""

from __future__ import annotations

from evals.clients.load_client import Result
from evals.executors.agent_task import AgentExecution
from evals.executors.agent_task import execute as execute_agent_task
from evals.executors.concurrency import execute as execute_concurrency
from evals.executors.network_fault import execute as execute_network_fault
from evals.scenarios.base import Scenario


def execute(scenario: Scenario, token: str) -> list[AgentExecution] | list[Result]:
    executors = {
        "agent-task": execute_agent_task,
        "concurrency": execute_concurrency,
        "network-fault": execute_network_fault,
    }
    return executors[scenario.mode](scenario, token)

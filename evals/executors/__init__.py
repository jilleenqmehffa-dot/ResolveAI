"""Dispatch a prepared scenario to its execution module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from evals.adapter.tool_execution_adapter import capture_tool_executions
from evals.clients.load_client import Result
from evals.executors.concurrency import execute as execute_concurrency
from evals.executors.network_fault import execute as execute_network_fault, fault_environment
from evals.executors.user_simulate.evaluation_launcher import (
    launch as launch_evaluation,
)
from evals.scenarios.base import Scenario

if TYPE_CHECKING:
    from evals.evaluators.agent_task import AgentExecution


def execute(scenario: Scenario, token: str) -> list[AgentExecution] | list[Result]:
    if scenario.mode == "agent-task" and scenario.fault is not None:
        with fault_environment(scenario) as proxy:
            results = launch_evaluation(scenario, token)
            for result in results:
                result.fault = scenario.fault
                result.fault_target = scenario.fault_target
                result.fault_ms = scenario.fault_ms
                result.proxy_observation = proxy.as_dict()
                database_url = scenario.setting("AGENT_DATABASE_URL")
                result.tool_execution_observation = (
                    capture_tool_executions(database_url, result.execution_ids)
                    if database_url else {"records": [], "error_type": "AGENT_DATABASE_URL_missing"}
                )
            return results
    executors = {
        "agent-task": launch_evaluation,
        "concurrency": execute_concurrency,
        "network-fault": execute_network_fault,
    }
    return executors[scenario.mode](scenario, token)

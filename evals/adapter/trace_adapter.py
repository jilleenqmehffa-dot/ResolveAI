"""Read complete LangSmith trace trees for an Agent execution ID.

The Agent uses its execution ID as the LangGraph thread ID. A retry may create
another root trace for the same execution, so callers receive every matching
trace instead of silently losing earlier attempts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from uuid import UUID

from dotenv import dotenv_values
from langsmith import Client

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _setting(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        value = dotenv_values(ENV_FILE).get(name)
    return value.strip() if value else None


@dataclass(frozen=True)
class TraceRun:
    id: str
    parent_run_id: str | None
    name: str
    run_type: str
    inputs: dict[str, Any]
    outputs: dict[str, Any] | None
    error: str | None
    start_time: datetime
    end_time: datetime | None
    metadata: dict[str, Any]
    total_tokens: int | None
    total_cost: float | None


@dataclass(frozen=True)
class AgentTrace:
    execution_id: str
    trace_id: str
    root_run_id: str
    runs: tuple[TraceRun, ...]


def _run_data(run: Any) -> TraceRun:
    extra = run.extra or {}
    metadata = extra.get("metadata") or {}
    return TraceRun(
        id=str(run.id),
        parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
        name=run.name,
        run_type=run.run_type,
        inputs=run.inputs or {},
        outputs=run.outputs,
        error=run.error,
        start_time=run.start_time,
        end_time=run.end_time,
        metadata=metadata,
        total_tokens=getattr(run, "total_tokens", None),
        total_cost=getattr(run, "total_cost", None),
    )


class TraceAdapter:
    """Fetch read-only LangSmith data without coupling the evaluator to the SDK."""

    def __init__(self, *, project: str | None = None, client: Client | None = None):
        self.project = project or _setting("LANGSMITH_PROJECT")
        if not self.project:
            raise ValueError("LANGSMITH_PROJECT is required to read traces")
        if client is None:
            api_key = _setting("LANGSMITH_API_KEY")
            if not api_key:
                raise ValueError("LANGSMITH_API_KEY is required to read traces")
            client = Client(
                api_key=api_key,
                api_url=_setting("LANGSMITH_ENDPOINT"),
                workspace_id=_setting("LANGSMITH_WORKSPACE_ID"),
            )
        self.client = client

    def list_traces(self, execution_id: str | UUID) -> list[AgentTrace]:
        """Return all trace trees for this execution, including retry attempts."""
        execution_id = str(UUID(str(execution_id)))
        filter_query = (
            'and(in(metadata_key, ["thread_id", "execution_id"]), '
            f'eq(metadata_value, "{execution_id}"))'
        )
        roots = self.client.list_runs(
            project_name=self.project,
            is_root=True,
            filter=filter_query,
        )
        traces: list[tuple[datetime, AgentTrace]] = []
        for root in roots:
            trace_id = str(root.trace_id or root.id)
            runs = list(
                self.client.list_runs(
                    project_name=self.project,
                    trace_id=trace_id,
                )
            )
            runs.sort(key=lambda run: (run.start_time, str(run.id)))
            traces.append(
                (
                    root.start_time,
                    AgentTrace(
                        execution_id=execution_id,
                        trace_id=trace_id,
                        root_run_id=str(root.id),
                        runs=tuple(_run_data(run) for run in runs),
                    ),
                )
            )
        traces.sort(key=lambda item: item[0])
        return [trace for _, trace in traces]

    def wait_for_traces(
        self,
        execution_id: str | UUID,
        *,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
    ) -> list[AgentTrace]:
        """Wait for asynchronous trace ingestion after an Agent run completes."""
        if timeout < 0 or poll_interval <= 0:
            raise ValueError("timeout must be nonnegative and poll_interval positive")
        deadline = monotonic() + timeout
        while True:
            traces = self.list_traces(execution_id)
            if traces or monotonic() >= deadline:
                return traces
            sleep(min(poll_interval, deadline - monotonic()))

"""Collect Agent execution state and database reachability independently of HTTP."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from evals.adapter.agent_api_adapter import TERMINAL
from evals.clients.execution_timing import completed_run_seconds
from services.agent_service.app.models.agent import AgentRun


@dataclass(frozen=True)
class AgentDatabaseObservation:
    execution_id: str | None
    reachable: bool
    found: bool
    status: str | None = None
    error_code: str | None = None
    error_type: str | None = None
    attempt_count: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def capture_agent_run(database_url: str, execution_id: str | None) -> AgentDatabaseObservation:
    """Read one run independently of the API, retaining database query failures."""
    if execution_id is not None:
        try:
            run_id = UUID(execution_id)
        except ValueError:
            return AgentDatabaseObservation(
                execution_id, False, False, error_type="InvalidExecutionId"
            )
    else:
        run_id = None
    url = make_url(database_url)
    if url.get_backend_name() == "postgresql":
        url = url.update_query_dict({"connect_timeout": "3"})
    try:
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                if run_id is None:
                    connection.execute(text("SELECT 1"))
                    return AgentDatabaseObservation(None, True, False)
                row = connection.execute(
                    select(AgentRun.status, AgentRun.error_code, AgentRun.attempt_count).where(
                        AgentRun.id == run_id
                    )
                ).one_or_none()
                return AgentDatabaseObservation(
                    execution_id, True, row is not None,
                    row.status.value if row else None,
                    row.error_code if row else None,
                    attempt_count=row.attempt_count if row else None,
                )
        finally:
            engine.dispose()
    except SQLAlchemyError as exc:
        return AgentDatabaseObservation(
            execution_id, False, False, error_type=type(exc).__name__
        )


def find_run_by_key(database_url: str, conversation_id: str, idempotency_key: str) -> str | None:
    """Recover an accepted run ID when a broker failure hid the /chat response."""
    url = make_url(database_url)
    if url.get_backend_name() == "postgresql":
        url = url.update_query_dict({"connect_timeout": "3"})
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            run_id = connection.scalar(
                select(AgentRun.id).where(
                    AgentRun.conversation_id == UUID(conversation_id),
                    AgentRun.idempotency_key == idempotency_key,
                )
            )
            return str(run_id) if run_id is not None else None
    finally:
        engine.dispose()


def check_agent_database(database_url: str) -> None:
    """Fail before submitting load if AgentRun results cannot be read."""
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM agent_runs LIMIT 1"))
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "Cannot read Agent database before load; check AGENT_DATABASE_URL "
            "and the PostgreSQL host port mapping"
        ) from exc
    finally:
        engine.dispose()


def collect_run_results(
    results: dict[int, dict], database_url: str, *, interval: float, timeout: float,
) -> tuple[datetime | None, datetime | None]:
    """Join HTTP submissions to durable terminal states and server-side timing."""
    pending = {
        UUID(row["execution_id"]): row
        for row in results.values() if row["execution_id"] is not None
    }
    if not pending:
        return None, None
    engine = create_engine(database_url, pool_pre_ping=True)
    deadline = time.monotonic() + timeout
    first_created_at = None
    last_finished_at = None
    try:
        while pending:
            with engine.connect() as connection:
                identifiers = list(pending)
                for offset in range(0, len(identifiers), 500):
                    batch = identifiers[offset:offset + 500]
                    rows = connection.execute(
                        select(
                            AgentRun.id, AgentRun.status, AgentRun.error_code,
                            AgentRun.created_at, AgentRun.finished_at,
                        ).where(AgentRun.id.in_(batch))
                    )
                    for run_id, status, error_code, created_at, finished_at in rows:
                        if first_created_at is None or created_at < first_created_at:
                            first_created_at = created_at
                        if status.value not in TERMINAL:
                            continue
                        result = pending.pop(run_id, None)
                        if result is None:
                            continue
                        result["status"] = status.value
                        result["error"] = error_code
                        if finished_at is None:
                            raise ValueError(f"terminal AgentRun {run_id} has no finished_at")
                        if last_finished_at is None or finished_at > last_finished_at:
                            last_finished_at = finished_at
                        result["total_seconds"] = completed_run_seconds({
                            "created_at": created_at.isoformat(),
                            "updated_at": finished_at.isoformat(),
                        })
            if not pending or time.monotonic() >= deadline:
                break
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
    finally:
        engine.dispose()
    for result in pending.values():
        result["status"] = "COMPLETION_TIMEOUT"
        result["error"] = "AgentRun did not reach a terminal state before the completion deadline"
    return first_created_at, last_finished_at

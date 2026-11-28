"""Read durable tool outcomes and retry counts for Agent fault evaluations."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from services.agent_service.app.models.agent import ToolExecution


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def capture_tool_executions(database_url: str, execution_ids: tuple[str, ...]) -> dict[str, Any]:
    """Keep a failed database read visible without losing the evaluation row."""
    if not execution_ids:
        return {"records": [], "error_type": None}
    try:
        run_ids = [UUID(value) for value in execution_ids]
    except ValueError:
        return {"records": [], "error_type": "InvalidExecutionId"}
    url = make_url(database_url)
    if url.get_backend_name() == "postgresql":
        url = url.update_query_dict({"connect_timeout": "3"})
    try:
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    select(
                        ToolExecution.run_id, ToolExecution.tool_call_id,
                        ToolExecution.tool_name, ToolExecution.status,
                        ToolExecution.attempt_count, ToolExecution.error_code,
                        ToolExecution.started_at, ToolExecution.finished_at,
                    ).where(ToolExecution.run_id.in_(run_ids))
                )
                records = [
                    {
                        "execution_id": str(run_id),
                        "tool_call_id": call_id,
                        "tool_name": name,
                        "status": status.value,
                        "attempt_count": attempts,
                        "error_code": error_code,
                        "started_at": _time(started_at),
                        "finished_at": _time(finished_at),
                    }
                    for run_id, call_id, name, status, attempts, error_code, started_at, finished_at in rows
                ]
                records.sort(key=lambda row: (row["execution_id"], row["tool_call_id"]))
                return {"records": records, "error_type": None}
        finally:
            engine.dispose()
    except SQLAlchemyError as exc:
        return {"records": [], "error_type": type(exc).__name__}

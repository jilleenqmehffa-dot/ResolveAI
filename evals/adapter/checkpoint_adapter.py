"""Observe durable LangGraph checkpoints for one AgentRun."""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError


def capture_checkpoint_count(database_url: str, execution_id: str | None) -> dict:
    if execution_id is None:
        return {"count": None, "error_type": "missing_execution_id"}
    url = make_url(database_url)
    if url.get_backend_name() == "postgresql":
        url = url.update_query_dict({"connect_timeout": "3"})
    try:
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                count = connection.scalar(
                    text("SELECT COUNT(*) FROM checkpoints WHERE thread_id = :thread_id"),
                    {"thread_id": execution_id},
                )
                return {"count": int(count or 0), "error_type": None}
        finally:
            engine.dispose()
    except SQLAlchemyError as exc:
        return {"count": None, "error_type": type(exc).__name__}

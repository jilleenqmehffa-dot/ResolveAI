"""LangGraph checkpoints in the Agent PostgreSQL database."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from services.agent_service.app.db.session import agent_database_url


def checkpoint_config(run_id: UUID) -> dict:
    return {"configurable": {"thread_id": str(run_id)}}


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    url = agent_database_url().replace("postgresql+psycopg://", "postgresql://", 1)
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
        yield saver

"""Connections to the Agent database; never use the Commerce session factory."""

import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def agent_database_url() -> str:
    url = os.environ.get("AGENT_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        raise RuntimeError("AGENT_DATABASE_URL must point to PostgreSQL")
    if url == os.environ.get("DATABASE_URL"):
        raise RuntimeError("Agent and Commerce databases must differ")
    return url


@lru_cache
def get_agent_engine() -> Engine:
    return create_engine(agent_database_url(), pool_pre_ping=True)


@lru_cache
def get_agent_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_agent_engine(), autoflush=False, expire_on_commit=False
    )

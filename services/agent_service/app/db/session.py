"""Connections to the Agent database; never use the Commerce session factory."""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from services.agent_service.app.core.config import get_settings


def agent_database_url() -> str:
    return get_settings().require_agent_database_url()


@lru_cache
def get_agent_engine() -> Engine:
    return create_engine(agent_database_url(), pool_pre_ping=True)


@lru_cache
def get_agent_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_agent_engine(), autoflush=False, expire_on_commit=False
    )

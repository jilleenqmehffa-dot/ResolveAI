"""Database sessions use the configured connection URL."""

from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.db.session import get_engine, get_session_factory


def test_session_factory_uses_database_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    try:
        with get_session_factory()() as session:
            assert session.scalar(select(1)) == 1
            assert session.bind is get_engine()
    finally:
        get_engine().dispose()
        get_session_factory.cache_clear()
        get_engine.cache_clear()
        get_settings.cache_clear()

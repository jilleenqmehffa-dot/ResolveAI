"""Independent Agent database migration environment."""

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from services.agent_service.app.models import agent_metadata

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


def agent_url() -> str:
    url = os.environ.get("AGENT_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        raise RuntimeError(
            "AGENT_DATABASE_URL must name a separate PostgreSQL database"
        )
    commerce_url = os.environ.get("DATABASE_URL")
    if commerce_url and url == commerce_url:
        raise RuntimeError("AGENT_DATABASE_URL must differ from DATABASE_URL")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=agent_url(),
        target_metadata=agent_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(agent_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=agent_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

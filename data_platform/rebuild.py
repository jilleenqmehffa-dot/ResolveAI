"""Rebuild an isolated Commerce database from a validated benchmark snapshot."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import Engine

from data_platform.core import source_path, validate_sources
from evals.runners.import_retail_json import build_rows
from services.commerce_service.app.models import Base


def rebuild_database(engine: Engine, source: Path | None = None) -> dict[str, int]:
    """Replace Commerce rows in one transaction; caller must confirm the target DB."""
    validate_sources()
    rows = build_rows(source or source_path("retail"))
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
        for table in Base.metadata.sorted_tables:
            if rows[table.name]:
                connection.execute(table.insert(), rows[table.name])
    return {name: len(values) for name, values in rows.items()}

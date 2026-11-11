"""Immutable receipts for benchmark data imports and rebuilds."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from data_platform.core import ROOT, manifest

DEFAULT_DIR = ROOT / "evals/ingestions"


def ingestions_dir() -> Path:
    return Path(os.getenv("DATA_PLATFORM_INGESTIONS_DIR", DEFAULT_DIR))


def record_ingestion(*, operation: str, database: str, counts: dict[str, int], changed: bool) -> dict[str, Any]:
    receipt = {
        "ingestion_id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "operation": operation,
        "database": database,
        "dataset_version": manifest()["version"],
        "changed": changed,
        "counts": counts,
    }
    directory = ingestions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{receipt['ingestion_id']}.json").open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return receipt


def list_ingestions() -> list[dict[str, Any]]:
    directory = ingestions_dir()
    if not directory.exists():
        return []
    return sorted(
        (json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*.json")),
        key=lambda receipt: receipt["created_at"], reverse=True,
    )

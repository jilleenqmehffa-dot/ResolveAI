"""Immutable, non-sensitive summaries for benchmark executions."""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from data_platform.core import ROOT, manifest, sha256

DEFAULT_RUNS_DIR = ROOT / "evals/runs"
_UNSET_VERSION = object()


def runs_dir() -> Path:
    return Path(os.getenv("DATA_PLATFORM_RUNS_DIR", DEFAULT_RUNS_DIR))


def _revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row.get("scores")]
    return {
        "total": len(rows),
        "scored": len(scored),
        "errors": sum(bool(row.get("error")) for row in rows),
        "statuses": dict(Counter(row.get("status", "UNKNOWN") for row in rows)),
        "mean_scores": {
            key: sum(float(row["scores"][key]) for row in scored) / len(scored)
            for key in ("task_success", "trajectory")
        } if scored else {},
    }


def record_run(
    rows: list[dict[str, Any]], *, task_path: Path, database_path: Path,
    split: str | None, output: Path, agent_version: str | None = None,
    rebuild_each_task: bool = False,
    llm_backend: str = "deepseek",
    mock_mode: str = "fast",
    mode: str = "agent-task",
    fault: str | None = None,
    fault_ms: int | None = None,
    concurrency: int | None = None,
    repeat: int = 1,
    scenario_config_path: Path | None = None,
    dataset_version: str | None | object = _UNSET_VERSION,
) -> dict[str, Any]:
    """Store a new run summary without customer data or credentials."""
    entry = {
        "run_id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_version": manifest()["version"] if dataset_version is _UNSET_VERSION else dataset_version,
        "sources": {"tasks_sha256": sha256(task_path), "retail_sha256": sha256(database_path)},
        "split": split,
        "rebuild_each_task": rebuild_each_task,
        "llm_backend": llm_backend,
        "mock_mode": mock_mode if llm_backend == "mock" else None,
        "mode": mode,
        "fault": fault,
        "fault_ms": fault_ms,
        "concurrency": concurrency,
        "repeat": repeat,
        "scenario_config_sha256": sha256(scenario_config_path) if scenario_config_path else None,
        "task_ids": [str(row["task_id"]) for row in rows],
        "agent_version": agent_version,
        "code_revision": _revision(),
        "evaluator_sha256": {
            name: sha256(ROOT / f"evals/evaluation/{name}.py")
            for name in ("task_success", "trajectory")
        },
        "result_file": str(output.resolve()),
        "summary": summarize_rows(rows),
    }
    directory = runs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{entry['run_id']}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(entry, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return entry


def get_run(run_id: str) -> dict[str, Any] | None:
    try:
        from uuid import UUID
        run_id = str(UUID(run_id))
    except ValueError:
        return None
    path = runs_dir() / f"{run_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def list_runs() -> list[dict[str, Any]]:
    directory = runs_dir()
    if not directory.exists():
        return []
    return sorted(
        (json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*.json")),
        key=lambda entry: entry["created_at"], reverse=True,
    )

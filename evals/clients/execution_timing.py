"""Measure completed Agent runs using server timestamps, excluding client polling."""

from __future__ import annotations

from datetime import datetime


def completed_run_seconds(response: dict) -> float:
    created = datetime.fromisoformat(response["created_at"])
    completed = datetime.fromisoformat(response["updated_at"])
    elapsed = (completed - created).total_seconds()
    # Wall clocks can move backwards during a run. Keep a completed task's
    # status intact rather than turning an invalid duration into a load error.
    return max(0.0, elapsed)

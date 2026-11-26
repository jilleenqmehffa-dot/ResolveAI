"""Load retail Bench cases and their private customer IDs for evaluation metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATASETS = Path(__file__).resolve().parents[1] / "scenarios/agent_task/datasets"


@dataclass(frozen=True)
class BenchCase:
    task_id: str
    customer_id: str
    message: str


def load_cases(split: str = "test", datasets: Path = DATASETS) -> list[BenchCase]:
    tasks = json.loads((datasets / "tasks.json").read_text(encoding="utf-8"))
    splits = json.loads((datasets / "split_tasks.json").read_text(encoding="utf-8"))
    customer_oracle = json.loads((datasets / "task_customers.json").read_text(encoding="utf-8"))
    if split not in splits:
        raise ValueError(f"Unknown split {split!r}; choose from {', '.join(splits)}")
    by_id = {str(task["id"]): task for task in tasks}
    cases = []
    for task_id in splits[split]:
        task = by_id[str(task_id)]
        instructions = task["user_scenario"]["instructions"]
        known = instructions["known_info"].strip()
        reason = instructions["reason_for_call"].strip()
        customer_id = customer_oracle[str(task_id)]
        cases.append(BenchCase(str(task_id), customer_id, f"{known}\n\n{reason}"))
    return cases

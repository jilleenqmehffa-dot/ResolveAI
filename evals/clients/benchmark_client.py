"""Map retail Bench tasks to authenticated Agent API requests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DATASETS = Path(__file__).resolve().parents[1] / "datasets"
EMAIL = re.compile(r"[\w.+-]+@[\w.-]*[\w-]", re.IGNORECASE)
ORDER = re.compile(r"#W\d+", re.IGNORECASE)
ZIP = re.compile(r"(?<!\d)\d{5}(?!\d)")


@dataclass(frozen=True)
class BenchCase:
    task_id: str
    customer_id: str
    message: str


def _customer_id(known_info: str, reason: str, users: dict, orders: dict) -> str:
    """Resolve identity from user-facing scenario text, never grading criteria."""
    text = f"{known_info} {reason}"
    lowered = text.casefold()
    by_email = {u["email"].casefold(): key for key, u in users.items()}
    matches = {
        by_email[email.casefold()]
        for email in EMAIL.findall(text)
        if email.casefold() in by_email
    }

    for key in users:
        if re.search(rf"(?<![\w]){re.escape(key)}(?![\w])", text, re.IGNORECASE):
            matches.add(key)
    for order_id in ORDER.findall(text):
        order = orders.get(order_id.upper())
        if order:
            matches.add(order["user_id"])

    if not matches:
        zips = set(ZIP.findall(known_info))
        for key, user in users.items():
            name = user["name"]
            full_name = f"{name['first_name']} {name['last_name']}".casefold()
            if full_name in lowered and user["address"]["zip"] in zips:
                matches.add(key)
    if len(matches) != 1:
        raise ValueError(
            f"Bench scenario identity is {'missing' if not matches else 'ambiguous'}: {known_info!r}"
        )
    return next(iter(matches))


def load_cases(split: str = "test", datasets: Path = DATASETS) -> list[BenchCase]:
    tasks = json.loads((datasets / "tasks.json").read_text(encoding="utf-8"))
    splits = json.loads((datasets / "split_tasks.json").read_text(encoding="utf-8"))
    db = json.loads((datasets / "db.json").read_text(encoding="utf-8"))
    if split not in splits:
        raise ValueError(f"Unknown split {split!r}; choose from {', '.join(splits)}")
    by_id = {str(task["id"]): task for task in tasks}
    cases = []
    for task_id in splits[split]:
        task = by_id[str(task_id)]
        instructions = task["user_scenario"]["instructions"]
        known = instructions["known_info"].strip()
        reason = instructions["reason_for_call"].strip()
        customer_id = _customer_id(known, reason, db["users"], db["orders"])
        cases.append(BenchCase(str(task_id), customer_id, f"{known}\n\n{reason}"))
    return cases

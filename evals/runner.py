"""Run Agent task, concurrency, or network fault evaluations from one CLI."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from data_platform.runs import record_run
from evals.evaluators.agent_task import evaluate as evaluate_agent_task
from evals.executors import execute
from evals.scenarios import prepare_scenario
from evals.scenarios.base import load_tasks, map_task, select_split
from evals.scenarios.network_fault import FAULTS

__all__ = ["load_tasks", "main", "map_task", "select_split"]

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def default_output(mode: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return REPORTS_DIR / mode / f"{stamp}-{uuid4().hex}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="override the selected scenario's YAML or JSON config")
    parser.add_argument(
        "--mode", choices=("agent-task", "concurrency", "network-fault"),
        default="agent-task", help="evaluation module (default: agent-task)",
    )
    parser.add_argument("--tasks", type=Path, help="JSON task array; overrides config tasks and split")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--task-id", help="select one task ID, regardless of config split")
    selection.add_argument("--all", action="store_true", help="select every task in the configured split")
    parser.add_argument("--output", type=Path, help="JSON result file; defaults to evals/reports/<mode>/")
    parser.add_argument("--dry-run", action="store_true", help="preview selection without requests")
    parser.add_argument("--llm-backend", choices=("deepseek", "mock"), help="override scenario model")
    parser.add_argument("--mock-mode", choices=("fast", "latency"), help="Mock LLM mode for concurrency tests")
    parser.add_argument("--limit", type=int, help="first N tasks from the selected split")
    parser.add_argument("--repeat", type=int, help="repetitions for load or fault modes")
    parser.add_argument("--concurrency", type=int, help="maximum simultaneous tasks")
    parser.add_argument("--fault", choices=FAULTS, help="override network-fault scenario fault")
    parser.add_argument("--fault-ms", type=int)
    parser.add_argument("--toxiproxy-api", help="Toxiproxy management API URL")
    parser.add_argument("--rebuild-each-task", action="store_true", help="restore pinned Commerce data before every task")
    parser.add_argument("--confirm-database", help="required with --rebuild-each-task; exact database name")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if (args.repeat is not None and args.repeat < 1) or (args.fault_ms is not None and args.fault_ms < 0) or (args.concurrency is not None and args.concurrency < 1):
        parser.error("--repeat and --concurrency must be positive; --fault-ms must be nonnegative")
    try:
        scenario = prepare_scenario(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    inputs = scenario.inputs
    if args.dry_run:
        print(json.dumps(scenario.preview(), ensure_ascii=False, indent=2))
        return 0
    output = args.output or default_output(args.mode)
    try:
        token = scenario.require_client_settings()
    except ValueError as exc:
        parser.error(str(exc))
    raw_results = execute(scenario, token)
    if scenario.mode == "agent-task":
        rows = [evaluate_agent_task(result) for result in raw_results]
    else:
        rows = [asdict(result) for result in raw_results]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    entry = record_run(
        rows, task_path=inputs.task_path, database_path=inputs.database_path,
        split=inputs.split,
        output=output, agent_version=scenario.setting("AGENT_VERSION"),
        rebuild_each_task=scenario.rebuild_each_task,
        llm_backend=scenario.llm_backend,
        mock_mode=scenario.mock_mode,
        mode=scenario.mode,
        fault=scenario.fault,
        fault_ms=scenario.fault_ms,
        concurrency=scenario.concurrency,
        repeat=scenario.repeat,
        scenario_config_path=inputs.config_path,
        dataset_version=inputs.dataset_version,
    )
    scored = [row for row in rows if row.get("scores") is not None]
    print(
        f"Wrote {len(rows)} results to {output} (run {entry['run_id']}); "
        f"scored: {len(scored)}; errors: {sum(bool(row['error']) for row in rows)}"
    )
    if scored:
        print(
            "Mean scores: "
            + ", ".join(
                f"{name}={sum(row['scores'][name] for row in scored) / len(scored):.3f}"
                for name in ("task_success", "trajectory")
            )
        )
    failed = {"ERROR", "POLL_TIMEOUT", "FAILED", "CANCELLED", "TIMED_OUT"}
    print(f"Status counts: {dict(Counter(row['status'] for row in rows))}")
    return 1 if any(row["error"] or row["status"] in failed for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())

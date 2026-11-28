"""Durable retry counts are available independently of LangSmith ingestion."""

import sqlite3
from uuid import uuid4

from evals.adapter.tool_execution_adapter import capture_tool_executions


def test_capture_tool_execution_retry_count(tmp_path):
    database = tmp_path / "agent.db"
    run_id = uuid4()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE tool_executions (run_id TEXT, tool_call_id TEXT, "
            "tool_name TEXT, status TEXT, attempt_count INTEGER, error_code TEXT, "
            "started_at TEXT, finished_at TEXT)"
        )
        connection.execute(
            "INSERT INTO tool_executions VALUES (?, 'tool-1', 'list_products', "
            "'SUCCEEDED', 3, NULL, '2026-11-28 00:00:00', '2026-11-28 00:00:02')",
            (run_id.hex,),
        )

    observed = capture_tool_executions(f"sqlite+pysqlite:///{database}", (str(run_id),))

    assert observed["error_type"] is None
    assert observed["records"][0]["attempt_count"] == 3
    assert observed["records"][0]["status"] == "SUCCEEDED"

"""A database outage is an observation, not a lost evaluation report."""

from evals.adapter.agent_db_adapter import capture_agent_run


def test_capture_agent_run_keeps_database_connection_failure(tmp_path):
    missing = tmp_path / "missing" / "agent.db"
    observation = capture_agent_run(f"sqlite+pysqlite:///{missing}", None)

    assert observation.reachable is False
    assert observation.found is False
    assert observation.error_type == "OperationalError"

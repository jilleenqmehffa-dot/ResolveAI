"""Agent settings use the repository env file and validate execution limits."""

import pytest
from pydantic import ValidationError

from services.agent_service.app.core import config


def test_settings_read_env_file_with_process_override(monkeypatch, tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "AGENT_DATABASE_URL=postgresql://agent-from-file\n"
        "DATABASE_URL=postgresql://commerce\n"
        "AGENT_RUN_TIMEOUT_SECONDS=30\n"
    )
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AGENT_DATABASE_URL", "postgresql://agent-from-process")

    settings = config.get_settings()

    assert settings.require_agent_database_url() == "postgresql://agent-from-process"
    assert settings.agent_run_timeout_seconds == 30


def test_settings_reject_shared_database_and_invalid_attempt_limit(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("AGENT_DATABASE_URL", "postgresql://same")
    monkeypatch.setenv("DATABASE_URL", "postgresql://same")
    with pytest.raises(RuntimeError, match="databases must differ"):
        config.get_settings().require_agent_database_url()

    monkeypatch.setenv("AGENT_MAX_ATTEMPTS", "0")
    with pytest.raises(ValidationError, match="agent_max_attempts"):
        config.get_settings()

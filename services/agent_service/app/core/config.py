"""Settings shared by the Agent API, worker, and database migrations."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", str_strip_whitespace=True)

    agent_database_url: str | None = None
    database_url: str | None = None
    rabbitmq_url: str = "amqp://guest:guest@localhost//"
    commerce_base_url: str = "http://localhost:8001"
    agent_internal_token: str | None = None
    agent_max_attempts: int = Field(default=3, ge=1)
    agent_run_timeout_seconds: float = Field(default=120, gt=0)
    agent_retry_base_seconds: float = Field(default=1, gt=0)
    agent_retry_max_seconds: float = Field(default=10, gt=0)
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-flash"

    def require_agent_database_url(self) -> str:
        url = self.agent_database_url
        if not url or not url.startswith("postgresql"):
            raise RuntimeError("AGENT_DATABASE_URL must point to PostgreSQL")
        if url == self.database_url:
            raise RuntimeError("Agent and Commerce databases must differ")
        return url


def get_settings() -> Settings:
    """Read current settings so tests and separate processes see their own environment."""
    return Settings(_env_file=PROJECT_ROOT / ".env")

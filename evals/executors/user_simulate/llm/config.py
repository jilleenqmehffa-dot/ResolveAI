"""Read simulator-only model settings from the environment and root .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class SimulatorLLMSettings:
    api_key: str = field(repr=False)
    model: str = "deepseek-flash"
    temperature: float = 0.7
    timeout_seconds: float = 30.0
    max_retries: int = 0

    def __post_init__(self) -> None:
        if not self.api_key.strip() or self.api_key == "your-user-simulator-api-key":
            raise ValueError("USER_SIMULATOR_API_KEY is required in the environment or .env")
        if not self.model.strip():
            raise ValueError("USER_SIMULATOR_MODEL must be nonempty")
        if not isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("USER_SIMULATOR_TEMPERATURE must be between 0 and 2")
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("USER_SIMULATOR_TIMEOUT_SECONDS must be positive")
        if self.max_retries < 0:
            raise ValueError("USER_SIMULATOR_MAX_RETRIES must be nonnegative")


def load_settings(*, env_file: Path | None = None) -> SimulatorLLMSettings:
    """Process environment wins over .env; no API call occurs here."""
    values = dotenv_values(env_file or PROJECT_ROOT / ".env")

    def get(name: str, default: str = "") -> str:
        return (os.getenv(name) or values.get(name) or default).strip()

    return SimulatorLLMSettings(
        api_key=get("USER_SIMULATOR_API_KEY"),
        model=get("USER_SIMULATOR_MODEL", "deepseek-flash"),
        temperature=float(get("USER_SIMULATOR_TEMPERATURE", "0.7")),
        timeout_seconds=float(get("USER_SIMULATOR_TIMEOUT_SECONDS", "30")),
        max_retries=int(get("USER_SIMULATOR_MAX_RETRIES", "0")),
    )

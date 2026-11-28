"""Stop and restore only the local Agent Worker for crash recovery tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

COMPOSE_FILE = Path(__file__).resolve().with_name("docker-compose.yml")


def _compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        check=True, capture_output=True, text=True, timeout=60,
    )


def stop_worker() -> None:
    """Use zero graceful-stop time so the active task loses its process."""
    _compose("stop", "-t", "0", "agent-worker")


def start_worker() -> None:
    _compose("start", "agent-worker")

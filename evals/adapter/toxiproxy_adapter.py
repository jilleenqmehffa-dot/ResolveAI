"""Capture the selected Toxiproxy entry after a fault is installed."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from infra.toxiproxy.control import TARGETS, call


@dataclass(frozen=True)
class ProxyObservation:
    target: str
    name: str
    captured_at: str
    enabled: bool
    upstream: str
    toxics: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def capture_proxy(api: str, target: str) -> ProxyObservation:
    """Read actual proxy state rather than assuming the requested toxic took effect."""
    proxy = call(api, f"/proxies/{TARGETS[target]}")
    return ProxyObservation(
        target=target,
        name=proxy["name"],
        captured_at=datetime.now(UTC).isoformat(),
        enabled=proxy["enabled"],
        upstream=proxy["upstream"],
        toxics=[
            {
                "name": toxic["name"],
                "type": toxic["type"],
                "stream": toxic["stream"],
                "attributes": toxic.get("attributes", {}),
            }
            for toxic in proxy.get("toxics", [])
        ],
    )

"""Named fault cases with the dependency and injection phase made explicit."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FaultCase:
    target: str
    fault: str | None
    phase: str
    mock_mode: str = "fast"


CASES = {
    "client-disconnect": FaultCase("client-api", "down", "after_submit"),
    "commerce-disconnect": FaultCase("commerce", "down", "before_submit"),
    "rabbitmq-disconnect": FaultCase("rabbitmq", "down", "before_submit"),
    "worker-crash": FaultCase("worker", None, "after_running", "latency"),
    "postgres-disconnect": FaultCase("postgres", "down", "after_running", "latency"),
}

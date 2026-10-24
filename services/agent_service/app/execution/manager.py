"""Classify execution failures and choose safe recovery actions.

This module makes decisions only. A Worker must persist the chosen outcome and
perform retries or business-result verification; neither is wired up yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite

import httpx
from openai import APIConnectionError, APIStatusError
from pydantic import ValidationError

from services.agent_service.app.transport.commer_client import CommerceClientError


class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNCERTAIN_WRITE = "uncertain_write"


class RecoveryAction(str, Enum):
    RETRY = "retry"
    FAIL = "fail"
    VERIFY_RESULT = "verify_result"
    SUCCEED = "succeed"
    NEEDS_REVIEW = "needs_review"


class BusinessResult(str, Enum):
    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    """Attempt and wall-clock limits for one logical operation."""

    max_attempts: int = 3
    total_timeout_seconds: float = 60.0
    base_retry_delay_seconds: float = 1.0
    max_retry_delay_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        for name in (
            "total_timeout_seconds",
            "base_retry_delay_seconds",
            "max_retry_delay_seconds",
        ):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str
    failure_kind: FailureKind | None = None
    retry_after_seconds: float | None = None


def classify_failure(error: Exception, *, is_write: bool) -> FailureKind:
    """Treat an ambiguous write response as unknown until Commerce is checked."""
    if isinstance(error, CommerceClientError):
        status = error.status_code
        transient = (
            error.retryable
            or error.code == "transport_error"
            or status in (408, 429)
            or status is not None
            and status >= 500
        )
        if not transient:
            return FailureKind.PERMANENT
        if is_write and (status != 429):
            return FailureKind.UNCERTAIN_WRITE
        return FailureKind.TRANSIENT
    if isinstance(error, (httpx.RequestError, APIConnectionError, TimeoutError)):
        return FailureKind.UNCERTAIN_WRITE if is_write else FailureKind.TRANSIENT
    if isinstance(error, (httpx.HTTPStatusError, APIStatusError)):
        status = (
            error.response.status_code
            if isinstance(error, httpx.HTTPStatusError)
            else error.status_code
        )
        if status in (408, 429) or status >= 500:
            return (
                FailureKind.UNCERTAIN_WRITE
                if is_write and status != 429
                else FailureKind.TRANSIENT
            )
        return FailureKind.PERMANENT
    if isinstance(error, (ValidationError, ValueError)):
        return FailureKind.PERMANENT
    # Unknown failures have no established retry or idempotency contract.
    return FailureKind.PERMANENT


class ExecutionManager:
    """Decide what a Worker should do after an operation attempt fails."""

    def __init__(self, policy: RecoveryPolicy | None = None) -> None:
        self.policy = policy or RecoveryPolicy()

    def _retry_or_fail(
        self, *, attempt_count: int, elapsed_seconds: float, kind: FailureKind
    ) -> RecoveryDecision:
        if attempt_count >= self.policy.max_attempts:
            return RecoveryDecision(RecoveryAction.FAIL, "Attempt limit reached", kind)
        delay = min(
            self.policy.base_retry_delay_seconds * 2 ** min(attempt_count - 1, 20),
            self.policy.max_retry_delay_seconds,
        )
        if elapsed_seconds + delay >= self.policy.total_timeout_seconds:
            return RecoveryDecision(
                RecoveryAction.FAIL, "Execution time limit reached", kind
            )
        return RecoveryDecision(RecoveryAction.RETRY, "Transient failure", kind, delay)

    def decide_failure(
        self,
        error: Exception,
        *,
        is_write: bool,
        attempt_count: int,
        elapsed_seconds: float,
    ) -> RecoveryDecision:
        """Count the failed attempt; never replay an uncertain write directly."""
        _validate_progress(attempt_count, elapsed_seconds)
        kind = classify_failure(error, is_write=is_write)
        if kind is FailureKind.PERMANENT:
            return RecoveryDecision(RecoveryAction.FAIL, "Non-retryable failure", kind)
        if kind is FailureKind.UNCERTAIN_WRITE:
            if elapsed_seconds >= self.policy.total_timeout_seconds:
                return RecoveryDecision(
                    RecoveryAction.NEEDS_REVIEW,
                    "Write result is unknown and execution time limit was reached",
                    kind,
                )
            return RecoveryDecision(
                RecoveryAction.VERIFY_RESULT,
                "Check Commerce for the write result before any retry",
                kind,
            )
        return self._retry_or_fail(
            attempt_count=attempt_count, elapsed_seconds=elapsed_seconds, kind=kind
        )

    def decide_verified_write(
        self,
        result: BusinessResult,
        *,
        attempt_count: int,
        elapsed_seconds: float,
    ) -> RecoveryDecision:
        """Resume only after Commerce confirms whether the write took effect."""
        _validate_progress(attempt_count, elapsed_seconds)
        if result is BusinessResult.APPLIED:
            return RecoveryDecision(
                RecoveryAction.SUCCEED, "Commerce confirmed the write was applied"
            )
        if result is BusinessResult.UNKNOWN:
            return RecoveryDecision(
                RecoveryAction.NEEDS_REVIEW,
                "Commerce could not confirm the write result",
            )
        if result is BusinessResult.NOT_APPLIED:
            return self._retry_or_fail(
                attempt_count=attempt_count,
                elapsed_seconds=elapsed_seconds,
                kind=FailureKind.TRANSIENT,
            )
        raise ValueError(f"Unknown business result: {result!r}")


def _validate_progress(attempt_count: int, elapsed_seconds: float) -> None:
    if attempt_count < 1:
        raise ValueError("attempt_count must include the failed attempt")
    if not isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must be finite and non-negative")

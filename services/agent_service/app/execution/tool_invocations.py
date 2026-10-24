"""Persist one logical tool call and its attempts in the Agent database."""

from __future__ import annotations

import json
from asyncio import to_thread
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from services.agent_service.app.models.agent import ToolExecution, ToolExecutionStatus


@dataclass(frozen=True, slots=True)
class InvocationStart:
    should_invoke: bool
    status: ToolExecutionStatus
    attempt_count: int
    result_payload: dict[str, Any] | None
    error_code: str | None


class ToolInvocationStore:
    """Track calls by (run ID, tool call ID) using short database transactions.

    A RUNNING record is not replayed automatically. A recovery worker must first
    determine whether the old attempt is still active or has an unknown result.
    """

    def __init__(self, run_id: UUID, session_factory: sessionmaker[Session]) -> None:
        self.run_id = run_id
        self._session_factory = session_factory

    async def recover_interrupted_reads(self) -> None:
        """An expired run lease makes its in-flight read safe to try again."""
        await to_thread(self._recover_interrupted_reads)

    def _recover_interrupted_reads(self) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(
                update(ToolExecution)
                .where(
                    ToolExecution.run_id == self.run_id,
                    ToolExecution.status == ToolExecutionStatus.RUNNING,
                )
                .values(
                    status=ToolExecutionStatus.RETRY_PENDING,
                    error_code="worker_interrupted",
                    finished_at=_utc_now(),
                )
            )

    async def start(
        self, *, call_id: str, tool_name: str, args: dict[str, Any], max_attempts: int
    ) -> InvocationStart:
        return await to_thread(
            self._start,
            call_id=call_id,
            tool_name=tool_name,
            args=args,
            max_attempts=max_attempts,
        )

    def _start(
        self, *, call_id: str, tool_name: str, args: dict[str, Any], max_attempts: int
    ) -> InvocationStart:
        if not call_id or not tool_name or max_attempts < 1:
            raise ValueError(
                "call ID, tool name, and positive max attempts are required"
            )
        request_payload = _json_payload(args)
        with self._session_factory() as session, session.begin():
            session.execute(
                insert(ToolExecution)
                .values(
                    run_id=self.run_id,
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    status=ToolExecutionStatus.PENDING,
                    attempt_count=0,
                    max_attempts=max_attempts,
                    request_payload=request_payload,
                )
                .on_conflict_do_nothing(constraint="uq_tool_executions_run_call")
            )
            record = _locked_record(session, self.run_id, call_id)
            if (
                record.tool_name != tool_name
                or record.request_payload != request_payload
            ):
                raise ValueError("tool call ID was reused for different arguments")
            should_invoke = (
                record.status
                in (ToolExecutionStatus.PENDING, ToolExecutionStatus.RETRY_PENDING)
                and record.attempt_count < record.max_attempts
            )
            if should_invoke:
                record.attempt_count += 1
                record.status = ToolExecutionStatus.RUNNING
                record.started_at = _utc_now()
                record.finished_at = None
            return InvocationStart(
                should_invoke=should_invoke,
                status=record.status,
                attempt_count=record.attempt_count,
                result_payload=record.result_payload,
                error_code=record.error_code,
            )

    async def finish(
        self,
        *,
        call_id: str,
        attempt_count: int,
        status: ToolExecutionStatus,
        result_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        await to_thread(
            self._finish,
            call_id=call_id,
            attempt_count=attempt_count,
            status=status,
            result_payload=result_payload,
            error_code=error_code,
        )

    def _finish(
        self,
        *,
        call_id: str,
        attempt_count: int,
        status: ToolExecutionStatus,
        result_payload: dict[str, Any] | None,
        error_code: str | None,
    ) -> None:
        if status not in (
            ToolExecutionStatus.RETRY_PENDING,
            ToolExecutionStatus.SUCCEEDED,
            ToolExecutionStatus.FAILED,
            ToolExecutionStatus.UNKNOWN,
        ):
            raise ValueError("finish requires a completed or retry-pending status")
        with self._session_factory() as session, session.begin():
            record = _locked_record(session, self.run_id, call_id)
            if record.status is not ToolExecutionStatus.RUNNING:
                raise ValueError("tool call is not running")
            if record.attempt_count != attempt_count:
                raise ValueError("tool attempt is no longer current")
            record.status = status
            record.result_payload = (
                _json_payload(result_payload) if result_payload is not None else None
            )
            record.error_code = error_code
            record.finished_at = _utc_now()


def _locked_record(session: Session, run_id: UUID, call_id: str) -> ToolExecution:
    record = session.scalar(
        select(ToolExecution)
        .where(ToolExecution.run_id == run_id, ToolExecution.tool_call_id == call_id)
        .with_for_update()
    )
    if record is None:
        raise LookupError("tool invocation does not exist")
    return record


def _json_payload(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _utc_now() -> datetime:
    return datetime.now(UTC)

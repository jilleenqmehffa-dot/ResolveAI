"""Durable state transitions for Agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from services.agent_service.app.models.agent import (
    AgentRun,
    AgentRunStatus,
    Conversation,
    Message,
    MessageRole,
)

TERMINAL = frozenset(
    {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.TIMED_OUT,
    }
)


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    run_id: UUID
    conversation_id: UUID
    trigger_message_id: UUID
    customer_id: str
    user_message: str
    lease_token: str
    attempt_count: int
    created_at: datetime
    deadline_at: datetime
    resumed: bool


def submit_run(
    sessions: sessionmaker[Session],
    *,
    conversation_id: UUID,
    customer_id: str,
    content: str,
    idempotency_key: str,
    total_timeout_seconds: float,
) -> UUID:
    """Create one run per conversation/key while holding the conversation lock."""
    if not content.strip() or not idempotency_key.strip() or total_timeout_seconds <= 0:
        raise ValueError("message, idempotency key, and timeout are required")
    with sessions() as session, session.begin():
        conversation = session.get(Conversation, conversation_id, with_for_update=True)
        if conversation is None or conversation.customer_id != customer_id:
            raise LookupError("conversation not found")
        existing = session.scalar(
            select(AgentRun).where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing.id
        seq = conversation.next_message_seq
        conversation.next_message_seq += 1
        message = Message(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=content,
            seq=seq,
        )
        session.add(message)
        session.flush()
        run = AgentRun(
            conversation_id=conversation_id,
            trigger_message_id=message.id,
            idempotency_key=idempotency_key,
            status=AgentRunStatus.QUEUED,
            deadline_at=datetime.now(UTC) + timedelta(seconds=total_timeout_seconds),
        )
        session.add(run)
        session.flush()
        return run.id


def claim_run(
    sessions: sessionmaker[Session],
    run_id: UUID,
    *,
    lease_seconds: float,
    total_timeout_seconds: float,
    max_attempts: int,
) -> ClaimedRun | None:
    if lease_seconds <= 0 or total_timeout_seconds <= 0 or max_attempts < 1:
        raise ValueError("lease, timeout, and max attempts must be positive")
    now = datetime.now(UTC)
    with sessions() as session, session.begin():
        run = session.get(AgentRun, run_id, with_for_update=True)
        if run is None or run.status in TERMINAL:
            return None
        if (
            run.status is AgentRunStatus.RUNNING
            and run.lease_expires_at is not None
            and run.lease_expires_at > now
        ):
            return None
        if (
            run.status is AgentRunStatus.RETRY_WAIT
            and run.retry_at is not None
            and run.retry_at > now
        ):
            return None
        if run.deadline_at is None:
            run.deadline_at = now + timedelta(seconds=total_timeout_seconds)
        if run.deadline_at is not None and run.deadline_at <= now:
            run.status = AgentRunStatus.TIMED_OUT
            run.error_code = "execution_timeout"
            run.finished_at = now
            run.version += 1
            return None
        if run.attempt_count >= max_attempts:
            run.status = AgentRunStatus.FAILED
            run.error_code = "attempt_limit"
            run.finished_at = now
            run.version += 1
            return None

        recovered = run.status is AgentRunStatus.RUNNING
        token = str(uuid4())
        run.status = AgentRunStatus.RUNNING
        run.attempt_count += 1
        run.version += 1
        run.lease_token = token
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.retry_at = None
        run.started_at = run.started_at or now
        conversation = session.get(Conversation, run.conversation_id)
        message = session.get(Message, run.trigger_message_id)
        if conversation is None or message is None:
            raise RuntimeError("run has no conversation or trigger message")
        return ClaimedRun(
            run_id=run.id,
            conversation_id=run.conversation_id,
            trigger_message_id=run.trigger_message_id,
            customer_id=conversation.customer_id,
            user_message=message.content,
            lease_token=token,
            attempt_count=run.attempt_count,
            created_at=run.created_at,
            deadline_at=run.deadline_at,
            resumed=recovered or run.attempt_count > 1,
        )


def renew_lease(
    sessions: sessionmaker[Session], run_id: UUID, token: str, *, lease_seconds: float
) -> bool:
    with sessions() as session, session.begin():
        run = session.get(AgentRun, run_id, with_for_update=True)
        if (
            run is None
            or run.status is not AgentRunStatus.RUNNING
            or run.lease_token != token
        ):
            return False
        run.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        return True


def finish_run(
    sessions: sessionmaker[Session], run_id: UUID, token: str, answer: str
) -> bool:
    with sessions() as session, session.begin():
        # Serialize message numbering with submit_run. Lock the conversation
        # first to keep a consistent lock order across both write paths.
        conversation_id = session.scalar(
            select(AgentRun.conversation_id).where(AgentRun.id == run_id)
        )
        if conversation_id is None:
            return False
        conversation = session.get(Conversation, conversation_id, with_for_update=True)
        run = session.get(AgentRun, run_id, with_for_update=True)
        if (
            conversation is None
            or run is None
            or run.status is not AgentRunStatus.RUNNING
            or run.lease_token != token
        ):
            return False
        message = Message(
            conversation_id=run.conversation_id,
            role=MessageRole.ASSISTANT,
            content=answer,
            seq=conversation.next_message_seq,
        )
        conversation.next_message_seq += 1
        session.add(message)
        session.flush()
        run.response_message_id = message.id
        run.status = AgentRunStatus.SUCCEEDED
        run.result_text = answer
        run.error_code = None
        run.finished_at = datetime.now(UTC)
        run.lease_token = None
        run.lease_expires_at = None
        run.version += 1
        return True


def fail_run(
    sessions: sessionmaker[Session],
    run_id: UUID,
    token: str,
    *,
    error_code: str,
    retry_after_seconds: float | None = None,
    timed_out: bool = False,
) -> bool:
    with sessions() as session, session.begin():
        run = session.get(AgentRun, run_id, with_for_update=True)
        if (
            run is None
            or run.status is not AgentRunStatus.RUNNING
            or run.lease_token != token
        ):
            return False
        now = datetime.now(UTC)
        if timed_out or run.deadline_at is not None and run.deadline_at <= now:
            run.status = AgentRunStatus.TIMED_OUT
        elif retry_after_seconds is not None:
            run.status = AgentRunStatus.RETRY_WAIT
            run.retry_at = now + timedelta(seconds=retry_after_seconds)
        else:
            run.status = AgentRunStatus.FAILED
        run.error_code = error_code
        run.lease_token = None
        run.lease_expires_at = None
        run.finished_at = now if run.status in TERMINAL else None
        run.version += 1
        return True


def cancel_run(
    sessions: sessionmaker[Session], run_id: UUID, *, customer_id: str
) -> AgentRunStatus | None:
    with sessions() as session, session.begin():
        run = session.get(AgentRun, run_id, with_for_update=True)
        if run is None:
            return None
        conversation = session.get(Conversation, run.conversation_id)
        if conversation is None or conversation.customer_id != customer_id:
            return None
        if run.status not in TERMINAL:
            run.status = AgentRunStatus.CANCELLED
            run.error_code = "cancelled"
            run.finished_at = datetime.now(UTC)
            run.lease_token = None
            run.lease_expires_at = None
            run.version += 1
        return run.status


def due_run_ids(sessions: sessionmaker[Session], *, limit: int = 100) -> list[UUID]:
    now = datetime.now(UTC)
    with sessions() as session:
        return list(
            session.scalars(
                select(AgentRun.id)
                .where(
                    or_(
                        AgentRun.status.in_(
                            (AgentRunStatus.PENDING, AgentRunStatus.QUEUED)
                        ),
                        (AgentRun.status == AgentRunStatus.RETRY_WAIT)
                        & or_(AgentRun.retry_at.is_(None), AgentRun.retry_at <= now),
                        (AgentRun.status == AgentRunStatus.RUNNING)
                        & or_(
                            AgentRun.lease_expires_at.is_(None),
                            AgentRun.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(AgentRun.created_at)
                .limit(limit)
            )
        )

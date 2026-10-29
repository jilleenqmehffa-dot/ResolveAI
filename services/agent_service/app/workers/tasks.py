"""Celery entry points for durable Agent execution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from celery import Celery
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased, sessionmaker

from services.agent_service.app.agents.graph import create_agent_graph
from services.agent_service.app.core.config import get_settings
from services.agent_service.app.db.session import get_agent_session_factory
from services.agent_service.app.execution.checkpoints import (
    checkpoint_config,
    open_checkpointer,
)
from services.agent_service.app.execution.lifecycle import (
    ClaimedRun,
    claim_run,
    due_run_ids,
    fail_run,
    finish_run,
    renew_lease,
)
from services.agent_service.app.execution.manager import (
    ExecutionManager,
    RecoveryAction,
    RecoveryPolicy,
)
from services.agent_service.app.execution.tool_invocations import ToolInvocationStore
from services.agent_service.app.models.agent import (
    AgentRun,
    AgentRunStatus,
    Message,
    MessageRole,
)
from services.agent_service.app.transport.commer_client import (
    CommerceClient,
    CommerceClientError,
)

celery_app = Celery(
    "resolveai_agent",
    broker=get_settings().rabbitmq_url,
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "dispatch-due-agent-runs": {
            "task": "agent.scan_due_runs",
            "schedule": 5.0,
        }
    },
)


def _policy() -> RecoveryPolicy:
    settings = get_settings()
    return RecoveryPolicy(
        max_attempts=settings.agent_max_attempts,
        total_timeout_seconds=settings.agent_run_timeout_seconds,
        base_retry_delay_seconds=settings.agent_retry_base_seconds,
        max_retry_delay_seconds=settings.agent_retry_max_seconds,
    )


def _conversation_history(
    sessions: sessionmaker[Session], claim: ClaimedRun
) -> list[BaseMessage]:
    with sessions() as session:
        trigger = session.get(Message, claim.trigger_message_id)
        if trigger is None or trigger.conversation_id != claim.conversation_id:
            raise RuntimeError("trigger message is missing")
        question = aliased(Message)
        answer = aliased(Message)
        completed = list(
            session.execute(
                select(question, answer)
                .join(AgentRun, AgentRun.trigger_message_id == question.id)
                .join(answer, AgentRun.response_message_id == answer.id)
                .where(
                    AgentRun.conversation_id == claim.conversation_id,
                    AgentRun.status == AgentRunStatus.SUCCEEDED,
                    question.conversation_id == claim.conversation_id,
                    answer.conversation_id == claim.conversation_id,
                    question.seq < trigger.seq,
                    answer.seq < trigger.seq,
                )
                .order_by(question.seq.desc())
                .limit(10)
            )
        )
    earlier = sorted(
        (message for pair in completed for message in pair),
        key=lambda message: message.seq,
    )
    return [
        HumanMessage(content=item.content)
        if item.role is MessageRole.USER
        else AIMessage(content=item.content)
        for item in earlier
    ]


async def _heartbeat(
    sessions: sessionmaker[Session],
    claim: ClaimedRun,
    task: asyncio.Task,
    lease_seconds: float,
) -> None:
    while not task.done():
        await asyncio.sleep(lease_seconds / 3)
        if task.done():
            return
        try:
            retained = await asyncio.to_thread(
                renew_lease,
                sessions,
                claim.run_id,
                claim.lease_token,
                lease_seconds=lease_seconds,
            )
        except SQLAlchemyError:
            task.cancel()
            return
        if not retained:
            task.cancel()
            return


async def _execute_graph(
    claim: ClaimedRun,
    sessions: sessionmaker[Session],
    manager: ExecutionManager,
) -> str:
    store = ToolInvocationStore(claim.run_id, sessions)
    if claim.resumed:
        await store.recover_interrupted_invocations()
    config = checkpoint_config(claim.run_id)
    config["configurable"].update(
        {"execution_manager": manager, "tool_invocations": store}
    )
    commerce_url = get_settings().commerce_base_url
    async with (
        open_checkpointer() as saver,
        CommerceClient(
            commerce_url,
            claim.customer_id,
            agent_token=get_settings().agent_internal_token,
        ) as client,
    ):

        async def load_history(_state) -> list[BaseMessage]:
            return await asyncio.to_thread(_conversation_history, sessions, claim)

        graph = create_agent_graph(
            client, checkpointer=saver, context_loader=load_history
        )
        snapshot = await graph.aget_state(config)
        if snapshot.values:
            if snapshot.next:
                result = await graph.ainvoke(None, config=config)
            else:
                result = snapshot.values
        else:
            result = await graph.ainvoke(
                {
                    "messages": [HumanMessage(content=claim.user_message)],
                    "tool_call_count": 0,
                    "last_error": None,
                    "guard_result": None,
                },
                config=config,
            )
    last = result["messages"][-1]
    if not isinstance(last, AIMessage) or last.tool_calls or not last.content:
        raise ValueError("Agent run ended without a final answer")
    return str(last.content)


async def execute_run_async(
    run_id: UUID,
    *,
    sessions: sessionmaker[Session] | None = None,
    policy: RecoveryPolicy | None = None,
    lease_seconds: float = 60.0,
) -> None:
    sessions = sessions or get_agent_session_factory()
    policy = policy or _policy()
    claim = await asyncio.to_thread(
        claim_run,
        sessions,
        run_id,
        lease_seconds=lease_seconds,
        total_timeout_seconds=policy.total_timeout_seconds,
        max_attempts=policy.max_attempts,
    )
    if claim is None:
        return
    manager = ExecutionManager(policy)
    remaining = (claim.deadline_at - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        await asyncio.to_thread(
            fail_run,
            sessions,
            run_id,
            claim.lease_token,
            error_code="execution_timeout",
            timed_out=True,
        )
        return
    graph_task = asyncio.create_task(_execute_graph(claim, sessions, manager))
    heartbeat = asyncio.create_task(
        _heartbeat(sessions, claim, graph_task, lease_seconds)
    )
    try:
        async with asyncio.timeout(remaining):
            answer = await graph_task
    except TimeoutError:
        await asyncio.to_thread(
            fail_run,
            sessions,
            run_id,
            claim.lease_token,
            error_code="execution_timeout",
            timed_out=True,
        )
    except asyncio.CancelledError:
        # Cancellation or lease loss was recorded by the actor holding the row.
        return
    except Exception as exc:  # noqa: BLE001 - worker boundary records failures
        elapsed = (datetime.now(UTC) - claim.created_at).total_seconds()
        decision = manager.decide_failure(
            exc,
            is_write=False,
            attempt_count=claim.attempt_count,
            elapsed_seconds=elapsed,
        )
        retry_after = (
            decision.retry_after_seconds
            if decision.action is RecoveryAction.RETRY
            else None
        )
        code = exc.code if isinstance(exc, CommerceClientError) else type(exc).__name__
        await asyncio.to_thread(
            fail_run,
            sessions,
            run_id,
            claim.lease_token,
            error_code=code,
            retry_after_seconds=retry_after,
        )
    else:
        await asyncio.to_thread(finish_run, sessions, run_id, claim.lease_token, answer)
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


@celery_app.task(name="agent.execute_run")
def execute_run(run_id: str) -> None:
    asyncio.run(execute_run_async(UUID(run_id)))


@celery_app.task(name="agent.scan_due_runs")
def scan_due_runs() -> int:
    run_ids = due_run_ids(get_agent_session_factory())
    for run_id in run_ids:
        execute_run.delay(str(run_id))
    return len(run_ids)

"""Agent Service HTTP entry point."""

from __future__ import annotations

from datetime import datetime
from hmac import compare_digest
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from kombu.exceptions import OperationalError
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from services.agent_service.app.core.config import get_settings
from services.agent_service.app.db.session import get_agent_session_factory
from services.agent_service.app.execution.lifecycle import cancel_run, submit_run
from services.agent_service.app.models.agent import (
    AgentRun,
    AgentRunStatus,
    Conversation,
)
from services.agent_service.app.workers.tasks import celery_app, execute_run


class ChatRequest(BaseModel):
    conversation_id: UUID
    message: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)


class RunResponse(BaseModel):
    execution_id: UUID
    conversation_id: UUID
    status: AgentRunStatus
    attempt_count: int
    result: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


def _caller(
    x_agent_token: str = Header(), x_customer_id: str = Header(min_length=1)
) -> str:
    expected = get_settings().agent_internal_token
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Agent API token is not configured"
        )
    if not compare_digest(x_agent_token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Agent API token")
    return x_customer_id


def _run_response(run: AgentRun) -> RunResponse:
    return RunResponse(
        execution_id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        attempt_count=run.attempt_count,
        result=run.result_text,
        error_code=run.error_code,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def create_app() -> FastAPI:
    app = FastAPI(title="ResolveAI Agent Service", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> dict[str, str]:
        try:
            with get_agent_session_factory()() as session:
                session.execute(text("SELECT 1"))
            with celery_app.connection() as connection:
                connection.ensure_connection(max_retries=0)
            commerce_url = get_settings().commerce_base_url
            response = httpx.get(f"{commerce_url.rstrip('/')}/health", timeout=2.0)
            response.raise_for_status()
        except (
            OperationalError,
            SQLAlchemyError,
            httpx.HTTPError,
            OSError,
            RuntimeError,
        ) as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Agent dependency unavailable"
            ) from exc
        return {"status": "ready"}

    @app.post("/conversations", status_code=status.HTTP_201_CREATED)
    def create_conversation(customer_id: str = Depends(_caller)) -> dict[str, UUID]:
        with get_agent_session_factory()() as session, session.begin():
            conversation = Conversation(customer_id=customer_id)
            session.add(conversation)
            session.flush()
            return {"conversation_id": conversation.id}

    @app.post("/chat", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
    def chat(request: ChatRequest, customer_id: str = Depends(_caller)) -> RunResponse:
        sessions = get_agent_session_factory()
        try:
            run_id = submit_run(
                sessions,
                conversation_id=request.conversation_id,
                customer_id=customer_id,
                content=request.message,
                idempotency_key=request.idempotency_key,
                total_timeout_seconds=get_settings().agent_run_timeout_seconds,
            )
        except LookupError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Conversation not found"
            ) from exc
        try:
            execute_run.delay(str(run_id))
        except OperationalError:
            # The durable QUEUED row is picked up by the periodic scanner.
            pass
        with sessions() as session:
            run = session.get(AgentRun, run_id)
            return _run_response(run)

    @app.get("/executions/{run_id}", response_model=RunResponse)
    def get_execution(run_id: UUID, customer_id: str = Depends(_caller)) -> RunResponse:
        with get_agent_session_factory()() as session:
            run = session.scalar(
                select(AgentRun)
                .join(Conversation, AgentRun.conversation_id == Conversation.id)
                .where(AgentRun.id == run_id, Conversation.customer_id == customer_id)
            )
            if run is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found")
            return _run_response(run)

    @app.post("/executions/{run_id}/cancel", response_model=RunResponse)
    def cancel_execution(
        run_id: UUID, customer_id: str = Depends(_caller)
    ) -> RunResponse:
        state = cancel_run(get_agent_session_factory(), run_id, customer_id=customer_id)
        if state is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found")
        return get_execution(run_id, customer_id)

    return app


app = create_app()

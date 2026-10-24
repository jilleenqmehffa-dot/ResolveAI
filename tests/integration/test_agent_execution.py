"""Durable Agent run, checkpoint, and tool record on an isolated database."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.agent_service.app.agents.graph import create_agent_graph
from services.agent_service.app.db.session import (
    get_agent_engine,
    get_agent_session_factory,
)
from services.agent_service.app.execution.checkpoints import (
    checkpoint_config,
    open_checkpointer,
)
from services.agent_service.app.execution.lifecycle import (
    claim_run,
    due_run_ids,
    finish_run,
    submit_run,
)
from services.agent_service.app.execution.tool_invocations import ToolInvocationStore
from services.agent_service.app.models.agent import (
    AgentRun,
    Conversation,
    ToolExecution,
)
from services.agent_service.app.transport.commer_client import CommerceClient
from services.agent_service.app.workers import tasks

DATABASE_URL = os.environ.get("AGENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="AGENT_TEST_DATABASE_URL is not set"
)


class Model:
    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[{"id": "tool-1", "name": "list_products", "args": {}}],
            )
        return AIMessage(content="Done")


@pytest.mark.asyncio
async def test_run_checkpoint_and_tool_record(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DATABASE_URL", DATABASE_URL)
    engine = create_engine(DATABASE_URL)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions() as session, session.begin():
            conversation = Conversation(customer_id="execution-test-user")
            session.add(conversation)
            session.flush()
            conversation_id = conversation.id

        key = str(uuid4())
        run_id = submit_run(
            sessions,
            conversation_id=conversation_id,
            customer_id="execution-test-user",
            content="List products",
            idempotency_key=key,
            total_timeout_seconds=120,
        )
        assert run_id == submit_run(
            sessions,
            conversation_id=conversation_id,
            customer_id="execution-test-user",
            content="List products",
            idempotency_key=key,
            total_timeout_seconds=120,
        )
        claim = claim_run(
            sessions,
            run_id,
            lease_seconds=60,
            total_timeout_seconds=120,
            max_attempts=3,
        )
        assert claim is not None
        assert (
            claim_run(
                sessions,
                run_id,
                lease_seconds=60,
                total_timeout_seconds=120,
                max_attempts=3,
            )
            is None
        )

        config = checkpoint_config(run_id)
        config["configurable"]["tool_invocations"] = ToolInvocationStore(
            run_id, sessions
        )
        async with (
            open_checkpointer() as saver,
            CommerceClient(
                "http://commerce.test",
                "execution-test-user",
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[])),
            ) as client,
        ):
            graph = create_agent_graph(client, model=Model(), checkpointer=saver)
            result = await graph.ainvoke(
                {
                    "messages": [HumanMessage(content="List products")],
                    "tool_call_count": 0,
                    "last_error": None,
                    "guard_result": None,
                },
                config=config,
            )
            snapshot = await graph.aget_state(config)
            assert not snapshot.next
            assert snapshot.values["messages"][-1].content == "Done"

        # Simulate a worker exit after LangGraph completed but before the run
        # result was committed. The next worker must reuse the checkpoint.
        with sessions() as session, session.begin():
            run = session.get(AgentRun, run_id)
            run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        resumed = claim_run(
            sessions,
            run_id,
            lease_seconds=60,
            total_timeout_seconds=120,
            max_attempts=3,
        )
        assert resumed is not None and resumed.resumed
        async with (
            open_checkpointer() as saver,
            CommerceClient("http://commerce.test", "execution-test-user") as client,
        ):
            recovered_graph = create_agent_graph(
                client, model=Model(), checkpointer=saver
            )
            recovered = await recovered_graph.aget_state(checkpoint_config(run_id))
            assert recovered.values["messages"][-1].content == "Done"
            assert not recovered.next

        assert not finish_run(sessions, run_id, claim.lease_token, "stale")
        assert finish_run(
            sessions, run_id, resumed.lease_token, result["messages"][-1].content
        )
        assert not finish_run(sessions, run_id, claim.lease_token, "stale")
        with sessions() as session:
            run = session.get(AgentRun, run_id)
            record = session.query(ToolExecution).filter_by(run_id=run_id).one()
            assert run.status.value == "SUCCEEDED"
            assert run.attempt_count == 2
            assert record.status.value == "SUCCEEDED"
            assert record.attempt_count == 1
    finally:
        engine.dispose()


def test_api_submission_is_idempotent_and_customer_scoped(monkeypatch) -> None:
    from services.agent_service.app import main

    monkeypatch.setenv("AGENT_DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "test-internal-token")
    get_agent_session_factory.cache_clear()
    get_agent_engine.cache_clear()
    monkeypatch.setattr(main.execute_run, "delay", lambda _run_id: None)
    client = TestClient(main.create_app())
    headers = {"X-Agent-Token": "test-internal-token", "X-Customer-ID": "customer-one"}
    other = {"X-Agent-Token": "test-internal-token", "X-Customer-ID": "customer-two"}

    created = client.post("/conversations", headers=headers)
    assert created.status_code == 201
    payload = {
        "conversation_id": created.json()["conversation_id"],
        "message": "Hello",
        "idempotency_key": str(uuid4()),
    }
    first = client.post("/chat", headers=headers, json=payload)
    second = client.post("/chat", headers=headers, json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["execution_id"] == second.json()["execution_id"]
    run_id = first.json()["execution_id"]
    assert UUID(run_id) in due_run_ids(get_agent_session_factory())
    assert client.get(f"/executions/{run_id}", headers=other).status_code == 404
    cancelled = client.post(f"/executions/{run_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_worker_finishes_once_when_task_is_redelivered(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DATABASE_URL", DATABASE_URL)
    engine = create_engine(DATABASE_URL)
    sessions = sessionmaker(engine, expire_on_commit=False)

    class FinalModel:
        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, _messages):
            return AIMessage(content="Completed")

    actual_create_graph = create_agent_graph

    def scripted_graph(client, *, checkpointer, context_loader):
        return actual_create_graph(
            client,
            model=FinalModel(),
            checkpointer=checkpointer,
            context_loader=context_loader,
        )

    monkeypatch.setattr(tasks, "create_agent_graph", scripted_graph)
    try:
        with sessions() as session, session.begin():
            conversation = Conversation(customer_id="execution-test-user")
            session.add(conversation)
            session.flush()
            conversation_id = conversation.id
        run_id = submit_run(
            sessions,
            conversation_id=conversation_id,
            customer_id="execution-test-user",
            content="Hello",
            idempotency_key=str(uuid4()),
            total_timeout_seconds=120,
        )
        await tasks.execute_run_async(run_id, sessions=sessions)
        await tasks.execute_run_async(run_id, sessions=sessions)
        with sessions() as session:
            run = session.get(AgentRun, run_id)
            assert run.status.value == "SUCCEEDED"
            assert run.result_text == "Completed"
            assert run.attempt_count == 1
    finally:
        engine.dispose()

"""Agent ORM constraints on an isolated PostgreSQL test database.

Set AGENT_TEST_DATABASE_URL to a disposable database after running the Agent
Alembic upgrade against it. The tests use transactions and leave rows intact.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.agent_service.app.models import (
    AGENT_TABLES,
    AgentRun,
    Conversation,
    ConversationSummary,
    CustomerMemory,
    MemoryType,
    Message,
    MessageRole,
    ToolExecution,
    agent_metadata,
)

DATABASE_URL = os.environ.get("AGENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="AGENT_TEST_DATABASE_URL is not set"
)


@pytest.fixture(scope="module")
def engine():
    db = create_engine(DATABASE_URL)
    try:
        assert db.dialect.name == "postgresql"
        assert AGENT_TABLES <= set(inspect(db).get_table_names())
        yield db
    finally:
        db.dispose()


@pytest.fixture
def session(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(connection, expire_on_commit=False) as db:
            yield db
        transaction.rollback()


def seed(session):
    conversation = Conversation(customer_id="commerce-customer-1")
    message = Message(role=MessageRole.USER, content="Hello")
    conversation.messages.append(message)
    session.add(conversation)
    session.flush()
    return conversation, message


def test_relationships_and_defaults(session):
    conversation, message = seed(session)
    summary = ConversationSummary(
        conversation=conversation, content="Summary", last_message=message
    )
    run = AgentRun(conversation=conversation, trigger_message=message)
    tool = ToolExecution(
        tool_call_id="call-1",
        tool_name="orders.get",
        max_attempts=3,
        request_payload={"order": 1},
    )
    run.tool_executions.append(tool)
    expiry = datetime.now(UTC) + timedelta(days=30)
    memory = CustomerMemory(
        customer_id=conversation.customer_id,
        memory_type=MemoryType.PREFERENCE,
        content="Blue",
        source_message=message,
        expires_at=expiry,
    )
    session.add_all([summary, run, memory])
    session.flush()
    session.expire_all()

    loaded = session.get(Conversation, conversation.id)
    assert loaded.messages[0].conversation is loaded
    assert loaded.summary.last_message.id == message.id
    assert session.get(Message, message.id).covered_summaries[0].id == summary.id
    assert loaded.agent_runs[0].trigger_message.id == message.id
    assert loaded.agent_runs[0].tool_executions[0].agent_run.id == run.id
    assert session.get(Message, message.id).sourced_memories[0].id == memory.id
    assert session.get(CustomerMemory, memory.id).expires_at == expiry
    assert tool.attempt_count == 0
    assert tool.status.value == "PENDING"
    assert conversation.created_at.tzinfo is not None


def test_unique_summary_and_tool_call(session):
    conversation, message = seed(session)
    run = AgentRun(conversation=conversation, trigger_message=message)
    session.add_all(
        [
            ConversationSummary(conversation=conversation, content="One"),
            ToolExecution(
                agent_run=run, tool_call_id="same", tool_name="a", max_attempts=1
            ),
        ]
    )
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(ConversationSummary(conversation_id=conversation.id, content="Two"))
        session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            ToolExecution(
                run_id=run.id, tool_call_id="same", tool_name="b", max_attempts=1
            )
        )
        session.flush()


@pytest.mark.parametrize("attempts,maximum", [(-1, 2), (0, 0)])
def test_attempt_constraints(session, attempts, maximum):
    conversation, message = seed(session)
    run = AgentRun(conversation=conversation, trigger_message=message)
    session.add(run)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            ToolExecution(
                run_id=run.id,
                tool_call_id="bad",
                tool_name="a",
                attempt_count=attempts,
                max_attempts=maximum,
            )
        )
        session.flush()


def test_same_conversation_foreign_keys(session):
    first, message = seed(session)
    second = Conversation(customer_id="commerce-customer-1")
    session.add(second)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            ConversationSummary(
                conversation_id=second.id, content="Wrong", last_message_id=message.id
            )
        )
        session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(AgentRun(conversation_id=second.id, trigger_message_id=message.id))
        session.flush()
    assert first.id != second.id


def test_delete_policy_and_customer_id_is_external(session):
    conversation, message = seed(session)
    run = AgentRun(conversation=conversation, trigger_message=message)
    run.tool_executions.append(
        ToolExecution(tool_call_id="audit", tool_name="a", max_attempts=1)
    )
    memory = CustomerMemory(
        customer_id="commerce-customer-1",
        memory_type=MemoryType.PROFILE,
        content="Fact",
        source_message=message,
    )
    session.add_all([run, memory])
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(text("DELETE FROM messages WHERE id = :id"), {"id": message.id})
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(text("DELETE FROM agent_runs WHERE id = :id"), {"id": run.id})
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(
            text("DELETE FROM conversations WHERE id = :id"), {"id": conversation.id}
        )
    assert not any(
        fk.target_fullname.startswith("users.")
        for table in agent_metadata.tables.values()
        for fk in table.foreign_keys
    )
    assert not any(
        fk.parent.name == "customer_id"
        for table in agent_metadata.tables.values()
        for fk in table.foreign_keys
    )


def test_source_message_delete_sets_null(session):
    _conversation, message = seed(session)
    memory = CustomerMemory(
        customer_id="external",
        memory_type=MemoryType.OTHER,
        content="Note",
        source_message=message,
    )
    session.add(memory)
    session.flush()
    session.execute(text("DELETE FROM messages WHERE id = :id"), {"id": message.id})
    session.expire(memory)
    assert memory.source_message_id is None


def test_metadata_matches_migrated_schema(engine):
    assert set(agent_metadata.tables) == AGENT_TABLES
    inspector = inspect(engine)
    assert AGENT_TABLES <= set(inspector.get_table_names())
    assert {
        column["name"] for column in inspector.get_columns("tool_executions")
    } == set(agent_metadata.tables["tool_executions"].columns.keys())
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(text("count(*)"))
                .select_from(text("pg_type"))
                .where(text("typname = 'agent_tool_execution_status'"))
            )
            == 1
        )

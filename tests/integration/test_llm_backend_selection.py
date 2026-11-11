"""The requested model backend survives HTTP submission and Worker claiming."""

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.agent_service.app import main
from services.agent_service.app.execution.lifecycle import claim_run
from services.agent_service.app.models import AgentRun, agent_metadata


def test_per_run_backend_is_persisted_and_validated(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agent.sqlite'}")
    agent_metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "test-token")
    monkeypatch.setattr(main, "get_agent_session_factory", lambda: sessions)
    monkeypatch.setattr(main.execute_run, "delay", lambda _run_id: None)
    client = TestClient(main.create_app())
    headers = {"X-Agent-Token": "test-token", "X-Customer-ID": "customer-one"}
    conversation = client.post("/conversations", headers=headers).json()["conversation_id"]
    payload = {
        "conversation_id": conversation,
        "message": "Hello",
        "idempotency_key": str(uuid4()),
        "llm_backend": "mock",
    }
    response = client.post("/chat", headers=headers, json=payload)
    assert response.status_code == 202
    assert response.json()["llm_backend"] == "mock"
    assert client.get(f"/executions/{response.json()['execution_id']}", headers=headers).json()["llm_backend"] == "mock"
    # SQLite drops timezone information; let claim_run create an aware deadline.
    with sessions() as session, session.begin():
        session.get(AgentRun, UUID(response.json()["execution_id"])).deadline_at = None
    claim = claim_run(
        sessions, UUID(response.json()["execution_id"]),
        lease_seconds=60, total_timeout_seconds=120, max_attempts=3,
    )
    assert claim is not None and claim.llm_backend.value == "mock"
    assert client.post("/chat", headers=headers, json=payload).status_code == 202
    assert client.post(
        "/chat", headers=headers, json={**payload, "llm_backend": "deepseek"}
    ).status_code == 409
    assert client.post(
        "/chat", headers=headers, json={**payload, "idempotency_key": str(uuid4()), "llm_backend": "unknown"}
    ).status_code == 422
    default_response = client.post(
        "/chat", headers=headers,
        json={"conversation_id": conversation, "message": "Hello again", "idempotency_key": str(uuid4())},
    )
    assert default_response.status_code == 202
    assert default_response.json()["llm_backend"] == "deepseek"
    engine.dispose()

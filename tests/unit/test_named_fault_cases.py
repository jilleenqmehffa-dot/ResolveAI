"""Recovery cases preserve the original run and restore injected faults."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from evals.adapter.agent_api_adapter import Result
from evals.adapter.agent_db_adapter import AgentDatabaseObservation
from evals.executors import fault_cases


@pytest.mark.parametrize("name", [
    "client-disconnect", "commerce-disconnect", "rabbitmq-disconnect",
    "worker-crash", "postgres-disconnect",
])
def test_recovery_cases_keep_original_run_and_restore(monkeypatch, name):
    run_id = str(uuid4())
    conversation_id = str(uuid4())
    operations = []
    real_client = httpx.AsyncClient

    async def handler(request):
        assert request.url.path == "/chat"
        operations.append("resubmit")
        return httpx.Response(202, json={"execution_id": run_id, "status": "QUEUED"})

    def client(**kwargs):
        operations.append(f"client:{kwargs['base_url']}")
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    async def submit(*args):
        return Result(
            "one", "u1", 1, conversation_id=conversation_id, execution_id=run_id,
            status="QUEUED", request_customer_id="eval-anon:test",
        )

    async def poll(_client, result, _token, **_kwargs):
        result.status = "SUCCEEDED"

    monkeypatch.setattr(fault_cases.httpx, "AsyncClient", client)
    monkeypatch.setattr(fault_cases, "submit_case", submit)
    monkeypatch.setattr(fault_cases, "poll_existing_run", poll)
    monkeypatch.setattr(fault_cases, "_wait_until_running", lambda *_args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(fault_cases, "_probe_disconnected_client", lambda *_args: asyncio.sleep(0, result={"disconnected": True}))
    monkeypatch.setattr(fault_cases, "set_fault", lambda *_args: operations.append("set"))
    monkeypatch.setattr(fault_cases, "reset_fault", lambda *_args: operations.append("reset"))
    monkeypatch.setattr(fault_cases, "stop_worker", lambda: operations.append("stop"))
    monkeypatch.setattr(fault_cases, "start_worker", lambda: operations.append("start"))
    monkeypatch.setattr(fault_cases, "capture_proxy", lambda *_args: SimpleNamespace(as_dict=lambda: {"enabled": False}))
    monkeypatch.setattr(fault_cases, "capture_agent_run", lambda *_args: AgentDatabaseObservation(run_id, True, True, "SUCCEEDED", attempt_count=2))
    monkeypatch.setattr(fault_cases, "capture_checkpoint_count", lambda *_args: {"count": 2, "error_type": None})
    monkeypatch.setattr(fault_cases, "capture_tool_executions", lambda *_args: {
        "records": [{"attempt_count": 2}] if name == "commerce-disconnect" else [],
        "error_type": None,
    })
    scenario = SimpleNamespace(
        fault_case=name, outage_seconds=0.001, toxiproxy_api="http://toxiproxy.test",
        llm_backend="mock", mock_mode="latency",
        inputs=SimpleNamespace(
            tasks=[{"id": "one", "customer_id": "u1", "message": "test"}],
            customer_oracle={},
            config={"agent_base_url": "http://agent.test", "agent_proxy_url": "http://proxy.test"},
        ),
        setting=lambda _name: "sqlite://",
    )

    result = asyncio.run(fault_cases.execute_async(scenario, "test-token"))[0]

    assert result.execution_id == run_id
    assert result.status == "SUCCEEDED"
    assert all(result.checks.values())
    if name == "worker-crash":
        assert operations.index("stop") < operations.index("start")
    else:
        assert operations.index("set") < operations.index("reset")
    if name == "client-disconnect":
        assert "client:http://proxy.test" in operations
    if name == "rabbitmq-disconnect":
        assert "resubmit" in operations
        assert result.outage_observation["idempotent_resubmit"]["same_run"]

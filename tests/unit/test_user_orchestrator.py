"""The orchestrator controls turns, not the Agent's internal tool calls."""

import httpx
import pytest

from evals.executors.user_simulate.orchestrator import Orchestrator, State


@pytest.mark.asyncio
async def test_reuses_conversation_until_simulator_stops():
    requests = []
    simulated = []

    async def simulator(task, agent_reply, history):
        simulated.append((agent_reply, history))
        return ["I need to exchange my keyboard", "My order is #123", "STOP"][len(simulated) - 1]

    def handler(request):
        requests.append(request)
        if request.url.path == "/conversations":
            return httpx.Response(201, json={"conversation_id": "conversation-1"})
        if request.url.path == "/chat":
            turn = len([item for item in requests if item.url.path == "/chat"])
            return httpx.Response(202, json={"execution_id": f"run-{turn}", "status": "QUEUED"})
        turn = request.url.path.rsplit("-", 1)[-1]
        return httpx.Response(200, json={"status": "SUCCEEDED", "result": f"Agent reply {turn}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://agent.test") as client:
        result = await Orchestrator(client, "token", simulator=simulator, poll_interval=0.001).run(
            {"id": "task-1"}
        )

    assert result.state is State.STOPPED
    assert result.conversation_id == "conversation-1"
    assert result.execution_ids == ["run-1", "run-2"]
    assert result.history == [
        ("user", "I need to exchange my keyboard"), ("agent", "Agent reply 1"),
        ("user", "My order is #123"), ("agent", "Agent reply 2"),
    ]
    assert simulated[0] == (None, [])
    assert simulated[1][0] == "Agent reply 1"
    assert len([request for request in requests if request.url.path == "/conversations"]) == 1
    assert all(request.headers["x-customer-id"] == result.principal_id for request in requests)
    assert result.principal_id.startswith("eval-anon:")
    assert not any("commerce" in str(request.url) for request in requests)


@pytest.mark.asyncio
async def test_step_limit_and_agent_failure():
    async def simulator(task, agent_reply, history):
        return "Please continue"

    def successful_handler(request):
        if request.url.path == "/conversations":
            return httpx.Response(201, json={"conversation_id": "c"})
        if request.url.path == "/chat":
            return httpx.Response(202, json={"execution_id": "e", "status": "SUCCEEDED", "result": "Done"})
        raise AssertionError("unexpected polling")

    async with httpx.AsyncClient(transport=httpx.MockTransport(successful_handler), base_url="http://agent.test") as client:
        result = await Orchestrator(client, "token", simulator=simulator, max_steps=1).run({"id": 1})
    assert result.state is State.STEP_LIMIT
    assert len(result.history) == 2

    def failed_handler(request):
        if request.url.path == "/conversations":
            return httpx.Response(201, json={"conversation_id": "c"})
        return httpx.Response(202, json={"execution_id": "e", "status": "FAILED", "error_code": "agent_error"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(failed_handler), base_url="http://agent.test") as client:
        result = await Orchestrator(client, "token", simulator=simulator, max_errors=1).run({"id": 1})
    assert result.state is State.ERROR
    assert result.execution_ids == ["e"]
    assert "FAILED" in result.errors[0]

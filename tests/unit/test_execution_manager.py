"""Recovery decisions for read tools and the refund case write tool."""

import json
from collections.abc import Sequence

import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from services.agent_service.app.agents.graph import create_agent_graph
from services.agent_service.app.execution.manager import (
    BusinessResult,
    ExecutionManager,
    FailureKind,
    RecoveryAction,
    RecoveryPolicy,
    classify_failure,
)
from services.agent_service.app.execution.tool_invocations import (
    InvocationStart,
    ToolInvocationStore,
)
from services.agent_service.app.models.agent import ToolExecutionStatus
from services.agent_service.app.transport.commer_client import (
    CommerceClient,
    CommerceClientError,
)


def client_error(status: int) -> CommerceClientError:
    return CommerceClientError(
        "transient_error"
        if status in (408, 429) or status >= 500
        else "validation_error",
        f"HTTP {status}",
        retryable=status in (408, 429) or status >= 500,
        status_code=status,
    )


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_transient_reads_retry_within_limits(status: int) -> None:
    manager = ExecutionManager(
        RecoveryPolicy(
            max_attempts=3, total_timeout_seconds=10, base_retry_delay_seconds=1
        )
    )
    decision = manager.decide_failure(
        client_error(status), is_write=False, attempt_count=1, elapsed_seconds=0
    )
    assert decision.action is RecoveryAction.RETRY
    assert decision.failure_kind is FailureKind.TRANSIENT
    assert decision.retry_after_seconds == 1
    assert (
        manager.decide_failure(
            client_error(status), is_write=False, attempt_count=3, elapsed_seconds=0
        ).action
        is RecoveryAction.FAIL
    )
    assert (
        manager.decide_failure(
            client_error(status), is_write=False, attempt_count=1, elapsed_seconds=9
        ).action
        is RecoveryAction.FAIL
    )


@pytest.mark.parametrize("status", [404, 409, 422])
def test_business_and_validation_failures_do_not_retry(status: int) -> None:
    decision = ExecutionManager().decide_failure(
        client_error(status), is_write=False, attempt_count=1, elapsed_seconds=0
    )
    assert decision.action is RecoveryAction.FAIL
    assert decision.failure_kind is FailureKind.PERMANENT


def test_model_transport_and_http_failures_are_classified() -> None:
    request = httpx.Request("POST", "https://model.test")
    assert (
        classify_failure(
            httpx.ConnectError("disconnected", request=request), is_write=False
        )
        is FailureKind.TRANSIENT
    )
    assert classify_failure(TimeoutError(), is_write=False) is FailureKind.TRANSIENT
    for status, expected in (
        (429, FailureKind.TRANSIENT),
        (503, FailureKind.TRANSIENT),
        (400, FailureKind.PERMANENT),
    ):
        error = httpx.HTTPStatusError(
            "provider error",
            request=request,
            response=httpx.Response(status, request=request),
        )
        assert classify_failure(error, is_write=False) is expected


def test_write_result_is_verified_before_any_retry() -> None:
    manager = ExecutionManager(
        RecoveryPolicy(
            max_attempts=2, total_timeout_seconds=10, base_retry_delay_seconds=1
        )
    )
    disconnect = CommerceClientError("transport_error", "disconnected", retryable=True)
    assert classify_failure(disconnect, is_write=True) is FailureKind.UNCERTAIN_WRITE
    malformed = CommerceClientError(
        "invalid_response", "bad acknowledgement", retryable=False
    )
    assert classify_failure(malformed, is_write=True) is FailureKind.UNCERTAIN_WRITE
    assert (
        manager.decide_failure(
            disconnect, is_write=True, attempt_count=1, elapsed_seconds=1
        ).action
        is RecoveryAction.VERIFY_RESULT
    )
    assert (
        manager.decide_failure(
            client_error(408), is_write=True, attempt_count=1, elapsed_seconds=10
        ).action
        is RecoveryAction.NEEDS_REVIEW
    )
    assert (
        manager.decide_verified_write(
            BusinessResult.APPLIED, attempt_count=1, elapsed_seconds=2
        ).action
        is RecoveryAction.SUCCEED
    )
    assert (
        manager.decide_verified_write(
            BusinessResult.NOT_APPLIED, attempt_count=1, elapsed_seconds=2
        ).action
        is RecoveryAction.RETRY
    )
    assert (
        manager.decide_verified_write(
            BusinessResult.UNKNOWN, attempt_count=1, elapsed_seconds=2
        ).action
        is RecoveryAction.NEEDS_REVIEW
    )


class ScriptedModel:
    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, _messages: Sequence[BaseMessage]) -> AIMessage:
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[{"id": "call-1", "name": "list_products", "args": {}}],
            )
        return AIMessage(content="Done")


class MemoryInvocations(ToolInvocationStore):
    """Exercise the node's recorder contract without a PostgreSQL database."""

    def __init__(self) -> None:
        self.status = ToolExecutionStatus.PENDING
        self.attempt_count = 0
        self.result_payload = None
        self.error_code = None
        self.finished: list[ToolExecutionStatus] = []

    async def start(self, **_kwargs) -> InvocationStart:
        should_invoke = self.status in (
            ToolExecutionStatus.PENDING,
            ToolExecutionStatus.RETRY_PENDING,
        )
        if should_invoke:
            self.attempt_count += 1
            self.status = ToolExecutionStatus.RUNNING
        return InvocationStart(
            should_invoke,
            self.status,
            self.attempt_count,
            self.result_payload,
            self.error_code,
        )

    async def finish(
        self,
        *,
        status: ToolExecutionStatus,
        result_payload=None,
        error_code=None,
        **_kwargs,
    ) -> None:
        self.status = status
        self.result_payload = result_payload
        self.error_code = error_code
        self.finished.append(status)

    async def resolve_unknown(
        self,
        *,
        status: ToolExecutionStatus,
        result_payload=None,
        error_code=None,
        **_kwargs,
    ) -> None:
        assert self.status is ToolExecutionStatus.UNKNOWN
        self.status = status
        self.result_payload = result_payload
        self.error_code = error_code
        self.finished.append(status)


def initial_state():
    return {
        "messages": [HumanMessage(content="Show products")],
        "tool_call_count": 0,
        "last_error": None,
        "guard_result": None,
    }


@pytest.mark.asyncio
async def test_graph_records_retry_then_success_and_reuses_result() -> None:
    requests = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(503 if requests == 1 else 200, json=[])

    records = MemoryInvocations()
    manager = ExecutionManager(
        RecoveryPolicy(base_retry_delay_seconds=0.001, max_retry_delay_seconds=0.001)
    )
    config = {
        "configurable": {"execution_manager": manager, "tool_invocations": records}
    }
    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        first = await create_agent_graph(client, model=ScriptedModel()).ainvoke(
            initial_state(), config=config
        )
        second = await create_agent_graph(client, model=ScriptedModel()).ainvoke(
            initial_state(), config=config
        )

    assert requests == 2
    assert records.attempt_count == 2
    assert records.finished == [
        ToolExecutionStatus.RETRY_PENDING,
        ToolExecutionStatus.SUCCEEDED,
    ]
    for result in (first, second):
        message = next(
            item for item in result["messages"] if isinstance(item, ToolMessage)
        )
        assert json.loads(message.content) == {"ok": True, "data": []}
        assert result["tool_call_count"] == 1


@pytest.mark.asyncio
async def test_graph_does_not_retry_permanent_commerce_error() -> None:
    requests = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(422)

    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        result = await create_agent_graph(client, model=ScriptedModel()).ainvoke(
            initial_state()
        )

    message = next(item for item in result["messages"] if isinstance(item, ToolMessage))
    assert requests == 1
    assert json.loads(message.content)["error"]["code"] == "validation_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [ToolExecutionStatus.RUNNING, ToolExecutionStatus.UNKNOWN]
)
async def test_graph_does_not_replay_unresolved_invocation(status) -> None:
    requests = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json=[])

    records = MemoryInvocations()
    records.status = status
    records.attempt_count = 1
    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        result = await create_agent_graph(client, model=ScriptedModel()).ainvoke(
            initial_state(), config={"configurable": {"tool_invocations": records}}
        )

    message = next(item for item in result["messages"] if isinstance(item, ToolMessage))
    assert requests == 0
    assert records.attempt_count == 1
    assert json.loads(message.content)["error"]["code"] == "invocation_not_replayable"


class RefundModel:
    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, _messages: Sequence[BaseMessage]) -> AIMessage:
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "refund-1",
                        "name": "mark_after_sales_refunded",
                        "args": {"case_id": "case-1"},
                    }
                ],
            )
        return AIMessage(content="Case outcome updated")


@pytest.mark.asyncio
async def test_uncertain_refund_is_verified_before_replay() -> None:
    requests: list[str] = []
    case = {"case_id": "case-1", "status": "PROCESSING", "resolution": None}

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        if request.method == "PUT":
            case.update(status="CLOSED", resolution="REFUNDED")
            raise httpx.ReadError("response lost", request=request)
        return httpx.Response(200, json=case)

    records = MemoryInvocations()
    async with CommerceClient(
        "http://commerce.test",
        "u1",
        agent_token="test-agent-token",
        transport=httpx.MockTransport(respond),
    ) as client:
        result = await create_agent_graph(client, model=RefundModel()).ainvoke(
            initial_state(), config={"configurable": {"tool_invocations": records}}
        )

    assert requests == ["PUT", "GET"]
    assert records.finished == [
        ToolExecutionStatus.UNKNOWN,
        ToolExecutionStatus.SUCCEEDED,
    ]
    message = next(item for item in result["messages"] if isinstance(item, ToolMessage))
    assert json.loads(message.content)["data"]["resolution"] == "REFUNDED"


@pytest.mark.asyncio
async def test_refund_retries_only_after_verified_not_applied() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200, json={"status": "PROCESSING", "resolution": None}
            )
        if requests.count("PUT") == 1:
            raise httpx.ReadError("response lost", request=request)
        return httpx.Response(200, json={"status": "CLOSED", "resolution": "REFUNDED"})

    records = MemoryInvocations()
    manager = ExecutionManager(
        RecoveryPolicy(base_retry_delay_seconds=0.001, max_retry_delay_seconds=0.001)
    )
    async with CommerceClient(
        "http://commerce.test",
        "u1",
        agent_token="test-agent-token",
        transport=httpx.MockTransport(respond),
    ) as client:
        result = await create_agent_graph(client, model=RefundModel()).ainvoke(
            initial_state(),
            config={
                "configurable": {
                    "tool_invocations": records,
                    "execution_manager": manager,
                }
            },
        )

    assert requests == ["PUT", "GET", "PUT"]
    assert records.status is ToolExecutionStatus.SUCCEEDED
    message = next(item for item in result["messages"] if isinstance(item, ToolMessage))
    assert json.loads(message.content)["ok"] is True


@pytest.mark.asyncio
async def test_unknown_refund_result_is_not_replayed() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        raise httpx.ReadError("unavailable", request=request)

    records = MemoryInvocations()
    async with CommerceClient(
        "http://commerce.test",
        "u1",
        agent_token="test-agent-token",
        transport=httpx.MockTransport(respond),
    ) as client:
        result = await create_agent_graph(client, model=RefundModel()).ainvoke(
            initial_state(), config={"configurable": {"tool_invocations": records}}
        )

    assert requests == ["PUT", "GET"]
    assert records.status is ToolExecutionStatus.UNKNOWN
    message = next(item for item in result["messages"] if isinstance(item, ToolMessage))
    assert json.loads(message.content)["error"]["code"] == "write_result_unknown"


@pytest.mark.asyncio
async def test_refund_write_requires_durable_invocation_store() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "CLOSED", "resolution": "REFUNDED"})

    async with CommerceClient(
        "http://commerce.test", "u1", transport=httpx.MockTransport(respond)
    ) as client:
        with pytest.raises(RuntimeError, match="durable invocation store"):
            await create_agent_graph(client, model=RefundModel()).ainvoke(
                initial_state()
            )
    assert requests == []

"""The evaluator can retrieve all LangSmith runs for one Agent execution."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from evals.adapter.trace_adapter import TraceAdapter


def run(name: str, *, trace_id=None, parent_run_id=None, seconds: int = 0):
    run_id = uuid4()
    return SimpleNamespace(
        id=run_id,
        trace_id=trace_id or run_id,
        parent_run_id=parent_run_id,
        name=name,
        run_type="tool" if parent_run_id else "chain",
        inputs={"order_id": "o1"} if parent_run_id else {"messages": []},
        outputs={"ok": True},
        error=None,
        start_time=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
        end_time=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds + 1),
        extra={"metadata": {"thread_id": "execution-id"}},
        total_tokens=None,
        total_cost=None,
    )


def test_list_traces_keeps_retry_attempts_and_child_runs() -> None:
    execution_id = uuid4()
    first = run("graph", seconds=0)
    second = run("graph", seconds=10)
    child = run("get_order", trace_id=second.id, parent_run_id=second.id, seconds=11)

    class FakeClient:
        def __init__(self):
            self.calls = []

        def list_runs(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("is_root"):
                return iter([second, first])
            return iter(
                [child, second] if kwargs["trace_id"] == str(second.id) else [first]
            )

    client = FakeClient()
    traces = TraceAdapter(project="bench", client=client).list_traces(execution_id)

    assert [trace.root_run_id for trace in traces] == [str(first.id), str(second.id)]
    assert [item.name for item in traces[1].runs] == ["graph", "get_order"]
    assert traces[1].runs[1].parent_run_id == str(second.id)
    assert traces[1].runs[1].inputs == {"order_id": "o1"}
    assert client.calls[0] == {
        "project_name": "bench",
        "is_root": True,
        "filter": (
            'and(in(metadata_key, ["thread_id", "execution_id"]), '
            f'eq(metadata_value, "{execution_id}"))'
        ),
    }
    assert all(call["project_name"] == "bench" for call in client.calls)


def test_wait_for_traces_retries_until_ingested(monkeypatch) -> None:
    adapter = TraceAdapter(project="bench", client=object())
    calls = 0

    def load(_execution_id):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else ["trace"]

    monkeypatch.setattr(adapter, "list_traces", load)
    monkeypatch.setattr("evals.adapter.trace_adapter.sleep", lambda _: None)
    assert adapter.wait_for_traces(uuid4(), timeout=1, poll_interval=0.1) == ["trace"]
    assert calls == 2


def test_invalid_execution_id_is_rejected() -> None:
    adapter = TraceAdapter(project="bench", client=object())
    with pytest.raises(ValueError):
        adapter.list_traces("not-a-uuid")


def test_trace_adapter_uses_langsmith_api_key(monkeypatch) -> None:
    client_args = {}

    def fake_client(**kwargs):
        client_args.update(kwargs)
        return object()

    monkeypatch.setenv("LANGSMITH_API_KEY", "shared-key")
    monkeypatch.setattr("evals.adapter.trace_adapter.Client", fake_client)

    TraceAdapter(project="bench")

    assert client_args["api_key"] == "shared-key"

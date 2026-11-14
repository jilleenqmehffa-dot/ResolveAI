"""DeepSeek configuration is explicit and model creation does not call the API."""

import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from langchain_deepseek import ChatDeepSeek

from services.agent_service.app.agents.llm import factory
from services.agent_service.app.agents.llm.mock import MockChatModel
from services.agent_service.app.core import config


def test_factory_locates_repository_root() -> None:
    assert config.PROJECT_ROOT == Path(__file__).resolve().parents[2]


def test_factory_uses_environment_key_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")

    model = factory.create_deepseek_model()

    assert isinstance(model, ChatDeepSeek)
    assert model.model_name == "deepseek-flash"
    assert model.api_key.get_secret_value() == "test-key"
    assert model.max_retries == 0


@pytest.mark.asyncio
async def test_mock_model_needs_no_key_and_reports_zero_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    model = factory.create_chat_model("mock")
    assert isinstance(model, MockChatModel)
    request = await model.bind_tools([]).ainvoke("Hello")
    assert request.tool_calls[0]["name"] == "list_products"
    answer = await model.ainvoke([ToolMessage(content="{}", tool_call_id="mock-list-products")])
    assert answer.content == "Simulated LLM response. No Commerce action was performed."
    assert answer.usage_metadata == {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0
    }


@pytest.mark.asyncio
async def test_mock_latency_waits_once_per_model_call_without_real_sleep(monkeypatch) -> None:
    waits = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr("services.agent_service.app.agents.llm.mock.random.uniform", lambda low, high: 4.5)
    model = factory.create_chat_model("mock", mock_mode="latency")
    first = await model.ainvoke("Hello")
    second = await model.ainvoke([ToolMessage(content="[]", tool_call_id="mock-list-products")])
    assert first.tool_calls[0]["name"] == "list_products"
    assert second.content.startswith("Simulated LLM response")
    assert waits == [4.5, 4.5]


def test_factory_requires_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        factory.create_deepseek_model()


def test_factory_reads_repository_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "DEEPSEEK_API_KEY=file-key\nDEEPSEEK_MODEL=deepseek-flash\n"
    )

    model = factory.create_deepseek_model()

    assert model.api_key.get_secret_value() == "file-key"
    assert model.model_name == "deepseek-flash"


def test_factory_rejects_invalid_retry_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    with pytest.raises(ValueError, match="timeout"):
        factory.create_deepseek_model(timeout=0)
    with pytest.raises(ValueError, match="max_retries"):
        factory.create_deepseek_model(max_retries=-1)

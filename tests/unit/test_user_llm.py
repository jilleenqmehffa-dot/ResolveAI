"""The simulated user's model is configured separately from the Agent model."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from evals.executors.user_simulate.llm import (
    SimulatorLLMSettings,
    create_user_model,
    generate_text,
    load_settings,
)


def test_settings_prefer_process_environment_over_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "USER_SIMULATOR_API_KEY=file-key\n"
        "USER_SIMULATOR_MODEL=file-model\n"
        "USER_SIMULATOR_TEMPERATURE=0.3\n"
        "USER_SIMULATOR_TIMEOUT_SECONDS=12\n"
        "USER_SIMULATOR_MAX_RETRIES=1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("USER_SIMULATOR_API_KEY", "process-key")
    monkeypatch.setenv("USER_SIMULATOR_MODEL", "deepseek-flash")
    settings = load_settings(env_file=env_file)
    assert settings.api_key == "process-key"
    assert settings.model == "deepseek-flash"
    assert (settings.temperature, settings.timeout_seconds, settings.max_retries) == (0.3, 12, 1)
    assert "process-key" not in repr(settings)

    model = create_user_model(settings)
    assert model.api_key.get_secret_value() == "process-key"
    assert model.model_name == "deepseek-flash"
    assert model.max_retries == 1


@pytest.mark.parametrize("changes", [
    {"api_key": "your-user-simulator-api-key"},
    {"model": " "},
    {"temperature": float("nan")},
    {"timeout_seconds": 0},
    {"max_retries": -1},
])
def test_invalid_model_configuration_is_rejected(changes):
    values = {"api_key": "test-key", "model": "deepseek-flash"} | changes
    with pytest.raises(ValueError):
        SimulatorLLMSettings(**values)


@pytest.mark.asyncio
async def test_generate_text_uses_injected_model_and_requires_text():
    class Model:
        async def ainvoke(self, messages):
            assert messages[0].content == "Hello"
            return AIMessage(content="  Hi  ")

    assert await generate_text([HumanMessage(content="Hello")], model=Model()) == "Hi"
    with pytest.raises(ValueError, match="messages must be nonempty"):
        await generate_text([], model=Model())

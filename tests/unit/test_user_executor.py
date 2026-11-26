"""The simulated user sees its scenario and conversation, not grading targets."""

import json

import pytest
from langchain_core.messages import AIMessage

from evals.executors.user_simulate import user_executor


class StubModel:
    def __init__(self, content: str):
        self.content = content
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return AIMessage(content=self.content)


@pytest.mark.asyncio
async def test_generates_next_turn_without_evaluation_criteria():
    model = StubModel("  I do not remember my email address.  ")
    task = {
        "id": "12",
        "user_scenario": {"instructions": {"known_info": "My name is Sam", "unknown_info": "I do not know my email"}},
        "evaluation_criteria": {"actions": [{"name": "private_target"}]},
    }

    message = await user_executor.generate_next_user_message(
        task, "What is your email?", history=[("user", "I need help")], model=model,
    )

    assert message == "I do not remember my email address."
    context = json.loads(model.messages[1].content)
    assert context["user_scenario"] == task["user_scenario"]
    assert context["latest_agent_reply"] == "What is your email?"
    assert context["conversation_history"] == [{"role": "user", "content": "I need help"}]
    assert "evaluation_criteria" not in model.messages[1].content


@pytest.mark.asyncio
async def test_rejects_missing_context_and_empty_model_reply():
    with pytest.raises(ValueError, match="user_scenario.instructions"):
        await user_executor.generate_next_user_message({}, "Hello", model=StubModel("Hi"))
    with pytest.raises(ValueError, match="agent_reply"):
        await user_executor.generate_next_user_message(
            {"user_scenario": {"instructions": {}}}, " ", model=StubModel("Hi")
        )
    with pytest.raises(ValueError, match="empty or non-text"):
        await user_executor.generate_next_user_message(
            {"user_scenario": {"instructions": {}}}, "Hello", model=StubModel(" ")
        )


def test_model_uses_dedicated_key(monkeypatch):
    monkeypatch.setenv("USER_SIMULATOR_API_KEY", "separate-key")
    monkeypatch.setenv("USER_SIMULATOR_MODEL", "deepseek-flash")
    model = user_executor.create_user_model()
    assert model.api_key.get_secret_value() == "separate-key"
    assert model.model_name == "deepseek-flash"

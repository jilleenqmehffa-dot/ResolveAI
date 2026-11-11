"""Deterministic local chat model for execution-path tests."""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class MockChatModel(BaseChatModel):
    """Return a fixed answer without network requests or token consumption."""

    response: str = "Simulated LLM response. No Commerce action was performed."

    @property
    def _llm_type(self) -> str:
        return "resolveai-mock"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        answer = AIMessage(
            content=self.response,
            usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        return ChatResult(generations=[ChatGeneration(message=answer)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> "MockChatModel":
        del tools, kwargs
        return self

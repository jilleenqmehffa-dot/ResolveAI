"""Local chat model for execution-path and latency tests."""

import asyncio
import random
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from services.agent_service.app.agents.llm.types import MockMode


class MockChatModel(BaseChatModel):
    """Make one read-only Commerce call, then answer without model API tokens."""

    response: str = "Simulated LLM response. No Commerce action was performed."
    mode: MockMode = MockMode.FAST

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
        del stop, run_manager, kwargs
        if self.mode is MockMode.LATENCY:
            time.sleep(random.uniform(3.0, 6.0))
        return self._generate_response(messages)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.mode is MockMode.LATENCY:
            await asyncio.sleep(random.uniform(3.0, 6.0))
        return self._generate_response(messages)

    def _generate_response(self, messages: list[BaseMessage]) -> ChatResult:
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        answer = (
            AIMessage(content=self.response, usage_metadata=usage)
            if any(isinstance(message, ToolMessage) for message in messages)
            else AIMessage(
                content="",
                tool_calls=[{"id": "mock-list-products", "name": "list_products", "args": {}}],
                usage_metadata=usage,
            )
        )
        return ChatResult(generations=[ChatGeneration(message=answer)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> "MockChatModel":
        del tools, kwargs
        return self

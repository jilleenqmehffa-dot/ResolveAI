"""Invoke a simulator model and require one plain-text response."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

from evals.executors.user_simulate.llm.factory import create_user_model


async def generate_text(
    messages: Sequence[BaseMessage], *, model: BaseChatModel | None = None,
) -> str:
    """Run one model turn; an injected model allows offline evaluation tests."""
    if not messages:
        raise ValueError("messages must be nonempty")
    response = await (model or create_user_model()).ainvoke(list(messages))
    if not isinstance(response.content, str) or not response.content.strip():
        raise ValueError("user simulator returned an empty or non-text response")
    return response.content.strip()

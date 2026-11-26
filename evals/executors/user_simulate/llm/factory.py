"""Construct the simulated user's model independently of the Agent model."""

from __future__ import annotations

from langchain_deepseek import ChatDeepSeek

from evals.executors.user_simulate.llm.config import SimulatorLLMSettings, load_settings


def create_user_model(settings: SimulatorLLMSettings | None = None) -> ChatDeepSeek:
    """Build a DeepSeek chat model; requests begin only when it is invoked."""
    settings = settings or load_settings()
    return ChatDeepSeek(
        model=settings.model,
        api_key=settings.api_key,
        temperature=settings.temperature,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )

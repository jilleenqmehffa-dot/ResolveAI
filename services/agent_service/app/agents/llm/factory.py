"""Create the DeepSeek chat model used by the Agent graph."""

from langchain_deepseek import ChatDeepSeek

from services.agent_service.app.core.config import get_settings


def create_deepseek_model(
    *,
    model: str | None = None,
    temperature: float = 0.0,
    timeout: float = 30.0,
    max_retries: int = 0,
) -> ChatDeepSeek:
    """Build a model; no API request is made until invoke/ainvoke is called.

    The API key comes from DEEPSEEK_API_KEY in the environment or root .env.
    The graph's execution manager owns retry policy, so SDK retries default to 0.
    """
    settings = get_settings()
    api_key = settings.deepseek_api_key
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY is required in the environment or .env")
    selected_model = model or settings.deepseek_model
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_retries < 0:
        raise ValueError("max_retries must not be negative")
    return ChatDeepSeek(
        model=selected_model,
        api_key=api_key,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )

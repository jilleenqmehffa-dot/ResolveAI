FROM ghcr.io/astral-sh/uv:0.12.3 AS uv

FROM python:3.12-slim AS builder
WORKDIR /app
COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.12-slim
ENV PATH="/app/.venv/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY data_platform /app/data_platform
USER 10001:10001
EXPOSE 8002
CMD ["uvicorn", "data_platform.api:app", "--host", "0.0.0.0", "--port", "8002"]

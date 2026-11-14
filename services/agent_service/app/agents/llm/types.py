"""Supported per-execution model backends."""

from enum import StrEnum


class LLMBackend(StrEnum):
    DEEPSEEK = "deepseek"
    MOCK = "mock"


class MockMode(StrEnum):
    FAST = "fast"
    LATENCY = "latency"

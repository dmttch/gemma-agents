"""Small dependency contracts used by orchestration and deterministic tests.

Tool functions remain ordinary callables; no base class is required to extend the
registry. Streaming is an optional capability, not a requirement on test models.
"""

from collections.abc import Callable
from typing import Protocol

from ollama import ChatResponse


class EmbeddingModel(Protocol):
    """Generate vectors independently of conversation orchestration."""

    def embed(self, content: str, model: str) -> list[float]:
        """Return the vector for content using the requested embedding model."""
        ...


class ChatModel(Protocol):
    """Minimal synchronous model capability required by the agent loop."""

    def chat(self, messages: list[dict], tools: list[Callable]) -> ChatResponse:
        """Return an assistant message and optional typed tool calls."""
        ...


class AgentModel(ChatModel, EmbeddingModel, Protocol):
    """Combined dependency injected into Runtime; adapters own their transport."""

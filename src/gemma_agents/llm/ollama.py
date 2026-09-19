"""Adapt synchronous Ollama chat, streaming, and embedding calls."""

from collections.abc import Callable
from typing import Any

from ollama import ChatResponse, Client


class OllamaLLM:
    """Wrap one Ollama client and accumulate streamed responses for the agent loop."""

    def __init__(
        self,
        model: str,
        host: str,
        keep_alive: str = "15m",
    ):
        """Configure the active model, connection timeout, and model keep-alive period.
        """
        self.model = model
        self.keep_alive = keep_alive

        self.client = Client(
            host=host,
            timeout=120,
        )

    def embed(self, content: str, model: str) -> list[float]:
        """Return one embedding, asking Ollama to reject oversized input, not truncate
        it.
        """
        response = self.client.embed(model=model, input=content, truncate=False)
        return response.embeddings[0]

    def validate_model(self, model: str) -> None:
        """Require an installed model advertising tool support, without downloading."""
        if not model:
            raise ValueError("Choisis un modèle : gemma-agents setup ou AGENT_MODEL.")
        installed = {item.model for item in self.client.list().models}
        if model not in installed:
            raise ValueError(f"Modèle absent : {model}. Lance gemma-agents setup.")
        details = self.client.show(model)
        if "tools" not in (details.capabilities or []):
            raise ValueError(f"Le modèle {model} ne déclare pas le support des outils.")

    def close(self) -> None:
        """Close the HTTP client underlying the Ollama connection."""
        self.client._client.close()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[Callable],
    ) -> ChatResponse:
        """Request a complete model response with the supplied messages and tools."""

        return self.client.chat(
            model=self.model,
            messages=messages,
            tools=tools,
            keep_alive=self.keep_alive,
            options={
                "temperature": 0.2,
            },
        )

    def chat_stream(self, messages: list[dict], tools: list[Callable],
                    emit: Callable) -> ChatResponse:
        """Emit content tokens and return accumulated content, thinking, and tool calls.

        Always close the response stream, including when an event callback raises
        to interrupt generation.
        """
        content, thinking, calls = [], [], []
        stream = self.client.chat(model=self.model, messages=messages, tools=tools,
                                  keep_alive=self.keep_alive, stream=True,
                                  options={"temperature": 0.2})
        try:
            for chunk in stream:
                if chunk.message.content:
                    content.append(chunk.message.content)
                    emit({"type": "token", "content": chunk.message.content})
                if chunk.message.thinking:
                    thinking.append(chunk.message.thinking)
                calls.extend(chunk.message.tool_calls or [])
        finally:
            stream.close()
        return ChatResponse(message={"role": "assistant", "content": "".join(content),
                                     "thinking": "".join(thinking),
                                     "tool_calls": calls})

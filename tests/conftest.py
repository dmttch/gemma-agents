"""Shared deterministic model doubles and isolated runtime fixtures."""

import pytest
from ollama import ChatResponse

from gemma_agents.config import BASE_DIR, Settings
from gemma_agents.runtime import Runtime


class FakeLLM:
    """Replay queued chat responses and return predictable two-dimensional embeddings.
    """

    def __init__(self, responses=()):
        """Copy scripted responses and initialize captured model-call history."""
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools):
        """Capture input messages and consume the next scripted response."""
        self.calls.append(messages)
        return self.responses.pop(0)

    def embed(self, content, model):
        """Separate Python-related text from other content for deterministic retrieval
        tests.
        """
        return [1.0, 0.0] if "python" in content.lower() else [0.0, 1.0]


def response(content="Terminé.", tools=()):
    """Build an Ollama assistant response with optional named tool calls."""
    return ChatResponse(message={
        "role": "assistant", "content": content,
        "tool_calls": [{"function": {"name": name, "arguments": args}}
                       for name, args in tools],
    })


@pytest.fixture
def settings(tmp_path):
    """Provide isolated workspace/storage paths and disable automatic repair attempts.
    """
    return Settings(model="test", ollama_host="http://localhost:11434",
                    workspace=tmp_path / "workspace", storage_dir=tmp_path / "storage",
                    database_path=tmp_path / "storage/agent.db",
                    system_prompt_path=BASE_DIR / "prompts/system.md",
                    api_token="test-token-with-at-least-24-characters",
                    repair_attempts=0)


@pytest.fixture
def runtime(settings):
    """Yield a runtime with a fake model and close its resources after the test."""
    with Runtime(settings, FakeLLM()) as runtime:
        yield runtime

"""Test bounded web retrieval and command-line task persistence."""

import httpx
import pytest
from click.testing import CliRunner

from gemma_agents.main import main
from gemma_agents.tools.web import WebTools


def test_web_search_uses_fixed_endpoint_and_bounds_results(monkeypatch):
    """Use only the configured search endpoint and return at most the requested results.
    """
    original_client = httpx.Client
    requests = []

    def handle(request):
        """Capture the outgoing search request and return two deterministic mock
        results.
        """
        requests.append(request)
        return httpx.Response(200, json={"results": [
            {"title": "Résultat", "url": "https://example.com", "content": "extrait"},
            {"title": "Autre résultat"},
        ]})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handle),
    ))
    tools = WebTools("https://search.example.com")
    result = tools.web_search("test", limit=1)
    assert requests[0].url.host == "search.example.com"
    assert requests[0].url.path == "/search"
    assert requests[0].url.params["q"] == "test"
    assert "NON FIABLES" in result
    assert "Autre résultat" not in result


def test_web_search_rejects_redirect_and_missing_configuration(monkeypatch):
    """Reject redirects and refuse web searches without an endpoint configuration."""
    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(
            302, headers={"Location": "http://127.0.0.1/private"})),
    ))
    with pytest.raises(httpx.HTTPStatusError):
        WebTools("https://search.example.com").web_search("test")
    with pytest.raises(ValueError, match="Configure"):
        WebTools("").web_search("test")


def test_cli_help_and_task_persistence(tmp_path, monkeypatch):
    """Show CLI help, persist tasks across invocations, and require a token to serve."""
    monkeypatch.setenv("AGENT_STORAGE", str(tmp_path / "storage"))
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path / "workspace"))
    runner = CliRunner()
    assert runner.invoke(main, ["--help"]).exit_code == 0
    result = runner.invoke(main, ["tasks", "add", "Inspecte le projet"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(main, ["tasks", "list"])
    assert result.exit_code == 0, result.output
    assert "Inspecte le projet" in result.output
    assert "pending" in result.output
    result = runner.invoke(main, ["serve"])
    assert result.exit_code != 0
    assert "AGENT_API_TOKEN" in result.output

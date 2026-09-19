"""Test durable agent state, tool boundaries, and workspace isolation."""

import json
import sqlite3
from dataclasses import replace

import pytest
from conftest import FakeLLM, response

from gemma_agents.agent.context import ContextBuilder, ContextLimitError
from gemma_agents.memory.database import Database
from gemma_agents.memory.semantic import SemanticMemory
from gemma_agents.runtime import Runtime, RuntimeBusyError
from gemma_agents.security.approvals import DenyApprover
from gemma_agents.security.gateway import ToolGateway
from gemma_agents.security.policy import SecurityPolicy
from gemma_agents.sessions.manager import SessionManager
from gemma_agents.skills import SkillLibrary
from gemma_agents.tasks import Planner
from gemma_agents.tools.filesystem import FileSystemTools


def test_agent_tools_plan_and_history_survive_restart(settings):
    """Verify file edits, plan progress, and conversation history survive runtime
    restart.
    """
    llm = FakeLLM([
        response(tools=[("set_plan", {"steps": ["Écrire un fichier"]}),
                        ("write_file", {"path": "result.txt", "content": "bonjour"})]),
        response(tools=[("update_plan", {"index": 0, "status": "completed"})]),
        response("Fichier écrit et vérifié."),
    ])
    with Runtime(settings, llm) as runtime:
        session = runtime.sessions.create()
        events = []
        result = runtime.run(session, "Crée un fichier", emit=events.append)
        assert result.status == "completed"
        assert (settings.workspace / "result.txt").read_text() == "bonjour"
        assert json.loads(Planner(runtime.database, session).get_plan())[0][
            "status"] == "completed"
        assert events[-1]["type"] == "final"
        assert llm.calls[1][-1]["role"] == "tool"
    with Runtime(settings, FakeLLM([response()])) as runtime:
        assert runtime.sessions.resume(session) == session
        assert runtime.sessions.history(session)[-1]["content"] == result.content
        assert runtime.run(session, "Continue").status == "completed"


def test_v1_database_migration_is_additive(tmp_path):
    """Preserve legacy messages while adding schema and enforcing workspace ownership.
    """
    path = tmp_path / "agent.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE sessions(id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
                                  created_at TEXT NOT NULL);
            CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, created_at TEXT NOT NULL,
                payload TEXT NOT NULL);
        """)
        db.execute("INSERT INTO sessions VALUES ('old', ?, '2026-01-01')",
                   (str(tmp_path),))
        db.execute("INSERT INTO messages(session_id, created_at, payload) "
                   "VALUES ('old', '2026-01-01', ?)",
                   (json.dumps({"role": "user", "content": "V1"}),))
    database = Database(path)
    assert database.load_messages("old")[0]["content"] == "V1"
    assert SessionManager(database, tmp_path).resume("old") == "old"
    with pytest.raises(ValueError, match="autre workspace"):
        SessionManager(database, tmp_path / "other").resume("old")


def test_gateway_denial_validation_unknown_tools_and_audit(runtime):
    """Ensure rejected and invalid calls leave audit records without modifying files."""
    session = runtime.sessions.create()
    gateway = ToolGateway(runtime.registry(session), SecurityPolicy(
        runtime.settings.workspace), DenyApprover(), runtime.database, session)
    assert "REFUSÉE" in gateway.execute("run_command", {"command": "uv run pytest"})
    assert "ERREUR" in gateway.execute("write_file", {"path": "x", "content": 42})
    assert "ERREUR" in gateway.execute("unknown", {})
    assert "ERREUR" in gateway.execute("read_file", {"path": "../secret"})
    assert not (runtime.settings.workspace / "x").exists()
    with runtime.database.connect() as db:
        rows = db.execute("SELECT outcome FROM audit ORDER BY id")
        outcomes = [row[0] for row in rows]
    assert outcomes == ["denied", "error", "error", "error"]


@pytest.mark.parametrize("command", ["/tmp/uv run x", "ls; pwd", "uv run x | tail",
                                     "python script.py", "pip install foo",
                                     "ls '\n'", "'"])
def test_shell_policy_rejects_bypasses(runtime, command):
    """Reject executable-path tricks, shell syntax, and disallowed package commands."""
    registry = runtime.registry(runtime.sessions.create())
    decision = SecurityPolicy(runtime.settings.workspace).evaluate(
        registry.get("run_command"), {"command": command},
    )
    assert not decision.allowed


def test_filesystem_symlink_hardlink_and_exact_replace(tmp_path):
    """Protect external targets and reject ambiguous or empty text replacements."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("secret")
    (workspace / "escape").symlink_to(secret)
    (workspace / "hardlink").hardlink_to(secret)
    tools = FileSystemTools(workspace)
    with pytest.raises(PermissionError):
        tools.read_file("escape")
    with pytest.raises(PermissionError):
        tools.write_file("hardlink", "changed", overwrite=True)
    tools.write_file("normal", "a a")
    assert "refusée" in tools.replace_text("normal", "a", "b")
    with pytest.raises(ValueError):
        tools.replace_text("normal", "", "b")
    assert secret.read_text() == "secret"


def test_semantic_retrieval_is_scoped_and_model_specific(runtime, tmp_path):
    """Rank matching memories without crossing workspace or embedding-model boundaries.
    """
    memory_id = runtime.memory.remember("Utiliser Python pour le projet")
    runtime.memory.remember("La couleur est bleue")
    assert runtime.memory.search("python", 1)[0]["id"] == memory_id
    other = SemanticMemory(runtime.database, tmp_path / "other", runtime.llm,
                           runtime.settings.embedding_model)
    assert other.search("python") == []
    changed = SemanticMemory(runtime.database, runtime.settings.workspace,
                             runtime.llm, "different-model")
    assert changed.search("python") == []
    assert other.forget(memory_id).startswith("0")
    assert runtime.memory.forget(memory_id).startswith("1")


def test_skills_reject_path_escape(tmp_path):
    """Exclude escaped skill files from listing and reject unsafe skill reads."""
    root = tmp_path / "skills"
    (root / "python").mkdir(parents=True)
    (root / "python/SKILL.md").write_text("# Python\nUse uv.")
    secret = tmp_path / "secret"
    secret.write_text("secret")
    (root / "evil").mkdir()
    (root / "evil/SKILL.md").symlink_to(secret)
    skills = SkillLibrary(root)
    assert "python" in skills.list_skills()
    assert "evil" not in skills.list_skills()
    assert "uv" in skills.read_skill("python")
    with pytest.raises(ValueError):
        skills.read_skill("../secret")
    with pytest.raises(PermissionError):
        skills.read_skill("evil")


def test_turn_limit_is_saved(settings):
    """Persist a limited result when model iterations exhaust the configured budget."""
    with Runtime(replace(settings, max_agent_steps=1), FakeLLM([
        response(tools=[("list_files", {})]),
    ])) as runtime:
        session = runtime.sessions.create()
        result = runtime.run(session, "Inspecte")
        assert result.status == "limited"
        assert runtime.sessions.history(session)[-1]["content"] == result.content


def test_interrupted_tool_calls_are_repaired(runtime):
    """Insert an unknown-result placeholder for the unanswered call before resuming."""
    session = runtime.sessions.create()
    runtime.sessions.save(session, {"role": "user", "content": "Travaille"})
    runtime.sessions.save(session, response(tools=[("list_files", {}),
                                                  ("read_file", {"path": "x"})])
                          .message.model_dump(exclude_none=True))
    runtime.sessions.save(session, {"role": "tool", "tool_name": "list_files",
                                   "content": "x"})
    runtime.llm.responses = [response()]
    runtime.run(session, "Reprends")
    history = runtime.sessions.history(session)
    assert history[3]["tool_name"] == "read_file"
    assert "interrompu" in history[3]["content"]


def test_context_keeps_tool_sequences_atomic(settings):
    """Drop old turns intact and reject a current request that cannot fit the budget."""
    prompt = settings.workspace.parent / "prompt.md"
    prompt.write_text("system")
    builder = ContextBuilder(prompt, settings.workspace, max_chars=4000)
    history = [{"role": "user", "content": "x" * 3500},
               {"role": "assistant", "content": "done"},
               {"role": "user", "content": "new"},
               {"role": "assistant", "content": "", "tool_calls": [
                   {"function": {"name": "read_file", "arguments": {}}}]},
               {"role": "tool", "content": "y" * 500}]
    messages = builder.build(history)
    assert messages[1:] == history[2:]
    with pytest.raises(ContextLimitError):
        builder.build([{"role": "user", "content": "z" * 5000}])


def test_runtime_rejects_concurrent_turn(runtime):
    """Refuse a new turn while another operation holds the runtime lock."""
    session = runtime.sessions.create()
    with runtime.lock, pytest.raises(RuntimeBusyError):
        runtime.run(session, "hello")


def test_workspace_cannot_contain_runtime_storage(settings):
    """Reject workspaces that would expose protected runtime storage to file tools."""
    with pytest.raises(ValueError, match="distinct"):
        replace(settings, workspace=settings.storage_dir.parent).prepare()

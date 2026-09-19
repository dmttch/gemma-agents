"""Release contracts: process exclusivity, migration recovery and CLI integration."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from conftest import FakeLLM, response

from gemma_agents.locking import WorkspaceBusyError, WorkspaceLease
from gemma_agents.main import main
from gemma_agents.memory.database import Database
from gemma_agents.memory.migrations import MIGRATIONS, SCHEMA_VERSION
from gemma_agents.runtime import Runtime
from gemma_agents.storage import Retention, inspect_database, restore, snapshot


def test_model_file_view_decodes_json_once_and_preserves_history(settings):
    """The model sees literal quotes/CRLF/backslashes, while storage keeps JSON."""
    from copy import deepcopy

    from gemma_agents.agent.context import ContextBuilder

    literal = 'def f():\r\n    """Docstring."""\r\n    return r"\\n"\r\n'
    encoded = json.dumps({"path": "sample.py", "start_line": 1, "end_line": 3,
                          "content": literal, "truncated": False,
                          "partial_line": False})
    history = [{"role": "user", "content": "Read sample.py"},
               response(tools=[("read_file", {"path": "sample.py"})])
               .message.model_dump(exclude_none=True),
               {"role": "tool", "tool_name": "read_file", "content": encoded}]
    original = deepcopy(history)
    builder = ContextBuilder(settings.system_prompt_path, settings.workspace)
    messages = builder.build(history)
    assert messages[-1]["content"].endswith(literal)
    assert history == original
    for invalid in ('ACTION REFUSÉE', '[]', '{"content": 42}'):
        assert builder.file_excerpt(invalid) == invalid


def test_denied_action_still_reports_the_operator_checks(settings):
    """A refusal keeps the task blocked without hiding the observed check result."""
    llm = FakeLLM([response(tools=[("run_command", {"command": "uv run pytest"})]),
                   response("Les tests ont réussi.")])
    with Runtime(replace(settings, repair_attempts=2), llm) as runtime:
        runtime.runner.run = Mock(return_value="EXIT CODE: 1\nassert 1 == 2")
        task = runtime.tasks.create("Corrige", ["uv run pytest -q"])
        runtime.permissions.grant("task", task, "uv run pytest -q")
        events = []
        result = runtime.run_task(task, emit=events.append)
        # The denied command stays visible, yet the granted check still ran once.
        assert (result.status, result.verification) == ("blocked", "failed")
        assert runtime.tasks.get(task)["status"] == "blocked"
        assert runtime.verifications(result.session_id).list()[0]["status"] == "failed"
        assert runtime.runner.run.call_count == 1
        assert not [event for event in events if event["type"] == "repair"]
        assert len(llm.calls) == 2


def test_workspace_lock_cross_process_and_storage_independent(settings):
    """Separate processes/storage roots cannot share a workspace; close releases it."""
    with Runtime(settings, FakeLLM()):
        other = replace(settings, storage_dir=settings.storage_dir / "other",
                        database_path=settings.storage_dir / "other/agent.db")
        with pytest.raises(WorkspaceBusyError):
            Runtime(other, FakeLLM())
        code = ("from pathlib import Path; from gemma_agents.locking import "
                "WorkspaceLease; WorkspaceLease(Path(__import__('sys').argv[1]))")
        result = subprocess.run([sys.executable, "-c", code, str(settings.workspace)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0
        assert "WorkspaceBusyError" in result.stderr
    with Runtime(settings, FakeLLM()):
        pass


def test_lease_released_after_kill(tmp_path):
    """SIGKILL does not leave a stale lock requiring manual deletion."""
    code = ("from pathlib import Path; import sys,time; "
            "from gemma_agents.locking import WorkspaceLease; "
            "lease=WorkspaceLease(Path(sys.argv[1])); "
            "print('ready',flush=True); time.sleep(60)")
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
                             stdout=subprocess.PIPE, text=True)
    try:
        import select
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(WorkspaceBusyError):
            WorkspaceLease(tmp_path)
    finally:
        child.kill()
        child.wait(timeout=10)
        child.stdout.close()
    WorkspaceLease(tmp_path).close()


def test_future_schema_is_untouched(tmp_path):
    """An older runtime cannot relabel or mutate a newer database."""
    path = tmp_path / "agent.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version=99")
    db.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="futur"):
        Database(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("version", range(5))
def test_each_legacy_schema_upgrade_preserves_history(tmp_path, version):
    """Upgrade every supported baseline and retain a readable pre-upgrade backup."""
    path = tmp_path / "agent.db"
    with closing(sqlite3.connect(path)) as db, db:
        for number in range(1, max(1, version) + 1):
            db.executescript(MIGRATIONS[number])
        db.execute(f"PRAGMA user_version={version}")
        db.execute("INSERT INTO sessions VALUES ('old', ?, '2020')", (str(tmp_path),))
        db.execute("INSERT INTO messages(session_id, created_at, payload) "
                   "VALUES ('old', '2020', ?)",
                   (json.dumps({"role": "user", "content": "preserved"}),))
    database = Database(path)
    assert database.load_messages("old")[0]["content"] == "preserved"
    assert inspect_database(path) == SCHEMA_VERSION
    backups = list(tmp_path.glob("*.backup"))
    assert len(backups) == 1
    assert inspect_database(backups[0]) == version
    Database(path)
    assert len(list(tmp_path.glob("*.backup"))) == 1


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    """A failing upgrade leaves both data and schema version at their prior state."""
    path = tmp_path / "agent.db"
    Database(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA user_version=4")
    monkeypatch.setitem(MIGRATIONS, 5,
                        "CREATE TABLE transient(id INTEGER); INVALID SQL;")
    with pytest.raises(sqlite3.OperationalError):
        Database(path)
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='transient'").fetchone()


def test_backup_restore_and_retention(settings, tmp_path):
    """Round-trip history and WAL data; revoke grants and preserve workspace files."""
    backup = tmp_path / "snapshot.db"
    with Runtime(settings, FakeLLM()) as runtime:
        session = runtime.sessions.create()
        runtime.sessions.save(session, {"role": "user", "content": "saved"})
        runtime.permissions.grant("session", session, "uv run pytest")
        (settings.workspace / "keep.txt").write_text("keep")
        snapshot(settings.database_path, backup)
        with pytest.raises(FileExistsError):
            snapshot(settings.database_path, backup)
        with pytest.raises(WorkspaceBusyError):
            restore(backup, settings.database_path)
        runtime.sessions.save(session, {"role": "user", "content": "later"})
    rescue = restore(backup, settings.database_path)
    assert rescue.exists()
    with Runtime(settings, FakeLLM()) as runtime:
        assert len(runtime.sessions.history(session)) == 1
        assert not runtime.permissions.list()
        other = runtime.sessions.create()
        task = runtime.tasks.create("test")
        runtime.tasks.finish(task, session, "completed", "ok")
        runtime.database.audit(session, "read_file", {}, "executed", "old")
        Retention(runtime.database, settings.workspace).delete_session(session)
        assert runtime.sessions.resume(other) == other
        assert not runtime.tasks.list()
        with runtime.database.connect() as db:
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert (settings.workspace / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize("status, code", [("completed", 0), ("blocked", 3),
                                         ("limited", 4), ("failed", 1)])
def test_cli_json_and_exit_status(settings, monkeypatch, status, code):
    """A script receives one JSON object and the actual runtime result status."""
    from gemma_agents.runtime import RunResult

    runtime = Runtime(settings, FakeLLM())
    runtime.run = Mock(return_value=RunResult("session", "result", status))
    monkeypatch.setattr("gemma_agents.main.Runtime", lambda settings: runtime)
    monkeypatch.setattr("gemma_agents.main.Settings.from_env", lambda: settings)
    result = CliRunner().invoke(main, ["--json", "run", "test"])
    assert result.exit_code == code, result.output
    assert json.loads(result.stdout)["status"] == status


def test_setup_and_doctor_failures_are_explicit(settings, monkeypatch):
    """Persist validated model selection and report missing prerequisites as failure."""
    monkeypatch.setenv("AGENT_STORAGE", str(settings.storage_dir))
    monkeypatch.setenv("AGENT_WORKSPACE", str(settings.workspace))
    monkeypatch.setattr("gemma_agents.llm.ollama.OllamaLLM.validate_model",
                        Mock(side_effect=ValueError("unsupported tools")))
    result = CliRunner().invoke(main, ["--json", "doctor"])
    assert result.exit_code == 1
    assert not json.loads(result.stdout)["ok"]


def test_startup_failure_releases_workspace(settings, monkeypatch):
    """A database construction failure does not strand the workspace lease."""
    with monkeypatch.context() as patch:
        patch.setattr("gemma_agents.runtime.Database",
                      Mock(side_effect=ValueError("invalid schema")))
        with pytest.raises(ValueError):
            Runtime(settings, FakeLLM())
    with Runtime(settings, FakeLLM([response()])) as runtime:
        assert runtime.run(runtime.sessions.create(), "test").status == "completed"


def test_operator_path_resolution_ignores_workspace(tmp_path, monkeypatch):
    """An executable planted by the project cannot shadow the operator's uv."""
    from gemma_agents.security.sandbox import SandboxRunner

    binary = tmp_path / "uv"
    binary.write_text("#!/bin/sh\nexit 99\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    runner = SandboxRunner(tmp_path)
    try:
        assert runner.executable("uv") != str(binary)
    finally:
        runner.close()


def test_root_instructions_do_not_consume_an_extra_step(runtime):
    """Root guidance is in the first model context, so the first edit can execute."""
    (runtime.settings.workspace / "AGENTS.md").write_text("Keep accents.")
    runtime.llm.responses = [response(tools=[("write_file", {
        "path": "result.txt", "content": "é"})]), response()]
    result = runtime.run(runtime.sessions.create(), "Crée le fichier")
    assert result.status == "completed"
    assert "Keep accents." in runtime.llm.calls[0][0]["content"]
    assert "Fichier écrit" in runtime.llm.calls[1][-1]["content"]


def test_refused_file_edit_cannot_be_reported_as_success(runtime):
    """A model saying done after an ambiguous replacement still produces blocked."""
    (runtime.settings.workspace / "file.txt").write_text("a a")
    runtime.llm.responses = [response(tools=[("replace_text", {
        "path": "file.txt", "old_text": "a", "new_text": "b"})]), response()]
    result = runtime.run(runtime.sessions.create(), "Modifie le fichier")
    assert result.status == "blocked"
    assert (runtime.settings.workspace / "file.txt").read_text() == "a a"


def test_corrupt_restore_never_changes_live_data(settings, tmp_path):
    """Reject invalid snapshots before publishing any replacement."""
    with Runtime(settings, FakeLLM()) as runtime:
        session = runtime.sessions.create()
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        restore(corrupt, settings.database_path)
    with Runtime(settings, FakeLLM()) as runtime:
        assert runtime.sessions.resume(session) == session


def test_prune_is_explicit_and_workspace_scoped(runtime, tmp_path):
    """Dry-run counts accurately and applying retention preserves other workspaces."""
    session = runtime.sessions.create()
    other = runtime.database.create_session(tmp_path / "other")
    for identity in (session, other):
        runtime.database.audit(identity, "read_file", {}, "executed", "old")
    with runtime.database.connect() as db:
        db.execute("UPDATE audit SET created_at='2000-01-01T00:00:00+00:00'")
    retention = Retention(runtime.database, runtime.settings.workspace)
    assert retention.prune_audit(90) == 1
    assert retention.prune_audit(90, apply=True) == 1
    with runtime.database.connect() as db:
        assert db.execute("SELECT session_id FROM audit").fetchall()[0][0] == other
    assert runtime.sessions.resume(session) == session


def test_setup_persists_only_a_validated_model(settings, monkeypatch):
    """Persist compatible selections and preserve them after invalid input."""
    from gemma_agents.config import Settings

    monkeypatch.setenv("AGENT_STORAGE", str(settings.storage_dir))
    monkeypatch.setenv("AGENT_WORKSPACE", str(settings.workspace))
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    models = Mock(models=[Mock(model="local:tools")])
    monkeypatch.setattr("ollama.Client.list", Mock(return_value=models))
    show = Mock(return_value=Mock(capabilities=["tools"]))
    monkeypatch.setattr("ollama.Client.show", show)
    result = CliRunner().invoke(main, ["--json", "setup", "--model", "local:tools"])
    assert result.exit_code == 0, result.output
    assert Settings.from_env().model == "local:tools"
    show.return_value = Mock(capabilities=["completion"])
    result = CliRunner().invoke(main, ["--json", "setup", "--model", "local:tools"])
    assert result.exit_code == 1
    assert Settings.from_env().model == "local:tools"

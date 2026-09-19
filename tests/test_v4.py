"""Regression tests for steering, scoped grants, review, repair, and live work views."""

import asyncio
import io
import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from conftest import FakeLLM, response
from prompt_toolkit.application import create_app_session
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from gemma_agents.changes import ChangeStore
from gemma_agents.control import RunCancelled, RunControl
from gemma_agents.project import ProjectTools
from gemma_agents.runtime import Runtime
from gemma_agents.security.approvals import DenyApprover
from gemma_agents.terminal import CommandCompleter, TerminalChat
from gemma_agents.terminal_work import WorkView


def test_project_search_nested_ignore_and_scoped_instructions(settings):
    """Respect nested ignore overrides and order scoped instructions from root to leaf.
    """
    settings.prepare()
    root = settings.workspace
    (root / "src").mkdir()
    (root / "skip").mkdir()
    (root / ".gitignore").write_text("*.log\nskip/\n")
    (root / "src/.gitignore").write_text("!keep.log\n")
    (root / "src/keep.log").write_text("needle")
    (root / "hidden.log").write_text("needle")
    (root / "skip/data").write_text("needle")
    (root / "AGENTS.md").write_text("Root instructions")
    (root / "src/AGENTS.md").write_text("Scoped instructions")
    project = ProjectTools(root)
    assert "src/keep.log:1:needle" in project.search_text("needle")
    assert "hidden.log" not in project.files()
    assert "skip/data" not in project.files()
    assert "Scoped" not in project.instructions()
    assert project.instructions("src/keep.log").index("Root") < (
        project.instructions("src/keep.log").index("Scoped"))


def test_references_completion_bounds_and_escape(runtime, tmp_path):
    """Expand quoted line references and reject workspace escapes and symlink targets.
    """
    root = runtime.settings.workspace
    (root / "with spaces.py").write_text("one\ntwo\nthree\n")
    project = runtime.project
    expanded = project.expand_references('Lis @"with spaces.py:2-2"')
    assert "two" in expanded and "three" not in expanded
    assert '@"with spaces.py"' in [c.text for c in CommandCompleter(runtime)
                                   .get_completions(Document("Lis @with"), None)]
    with pytest.raises(PermissionError):
        project.expand_references("Lis @../outside")
    outside = tmp_path / "secret"
    outside.write_text("secret")
    (root / "link").symlink_to(outside)
    with pytest.raises(PermissionError):
        project.expand_references("Lis @link")


def test_instructions_are_observed_before_file_write(runtime):
    """Defer a write until the model has received its applicable project instructions.
    """
    (runtime.settings.workspace / "sub").mkdir()
    (runtime.settings.workspace / "sub/AGENTS.md").write_text("Keep accents.")
    runtime.llm.responses = [
        response(tools=[("write_file", {"path": "sub/x", "content": "é"})]),
        response(tools=[("write_file", {"path": "sub/x", "content": "é"})]), response()]
    session = runtime.sessions.create()
    runtime.run(session, "Crée")
    assert "non exécuté" in runtime.llm.calls[1][-1]["content"]
    assert (runtime.settings.workspace / "sub/x").read_text() == "é"


def test_steering_skips_remaining_actions_and_reaches_model(runtime):
    """Skip pending actions after steering while keeping tool-call history well formed.
    """
    control = RunControl()
    runtime.llm.responses = [response(tools=[
        ("write_file", {"path": "first", "content": "ok"}),
        ("write_file", {"path": "second", "content": "no"})]), response("Reçu")]
    events = []

    def emit(event):
        """Inject a new user constraint immediately after the first successful write."""
        events.append(event)
        if (event["type"] == "tool_result"
                and event["content"].startswith("Fichier écrit")):
            control.steer("Ne crée pas second.")

    session = runtime.sessions.create()
    result = runtime.run(session, "Crée deux fichiers", emit=emit, control=control)
    assert result.status == "completed"
    assert not (runtime.settings.workspace / "second").exists()
    assert any(e["type"] == "steering" for e in events)
    assert "Ne crée pas second" in json.dumps(runtime.llm.calls[-1], ensure_ascii=False)
    history = runtime.sessions.history(session)
    assert history[3]["role"] == "tool"  # skipped call still has a paired result
    assert control.closed
    with pytest.raises(ValueError, match="terminé"):
        control.steer("Trop tard")


def test_pause_and_cancel_release_boundary_without_running_action():
    """Wake a paused worker on cancellation and exit through RunCancelled."""
    control = RunControl()
    control.pause()
    ready = threading.Event()
    stopped = threading.Event()

    def worker():
        """Signal readiness, then record cancellation at the paused action boundary."""
        ready.set()
        try:
            control.boundary()
        except RunCancelled:
            stopped.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert ready.wait(1)
    assert not stopped.is_set()
    control.cancel()
    thread.join(2)
    assert stopped.is_set() and not thread.is_alive()


def test_cancel_during_stream_saves_interrupted_status(runtime):
    """Persist streamed cancellation as interrupted and release the runtime lock."""
    control = RunControl()

    def stream(messages, tools, emit):
        """Cancel just before emitting a token to exercise cancellation during
        streaming.
        """
        control.cancel()
        emit({"type": "token", "content": "partial"})

    runtime.llm.chat_stream = stream
    session = runtime.sessions.create()
    with pytest.raises(RunCancelled):
        runtime.run(session, "Travaille", control=control)
    assert not runtime.lock.locked()
    with runtime.database.connect() as db:
        assert db.execute("SELECT status FROM runs").fetchone()[0] == "interrupted"


def test_repeated_calls_stop_before_third_execution(runtime):
    """Stop identical consecutive tool requests after two actual executions."""
    runtime.llm.responses = [response(tools=[("list_files", {})])] * 4
    session = runtime.sessions.create()
    assert runtime.run(session, "Inspecte").status == "limited"
    with runtime.database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 2


def test_permissions_are_exact_scoped_revocable_and_expire(runtime, monkeypatch):
    """Honor only exact, live command grants for the selected scope and workspace."""
    runner = Mock(return_value="EXIT CODE: 0\nok")
    monkeypatch.setattr(runtime.runner, "run", runner)
    session = runtime.sessions.create()
    other = runtime.sessions.create()
    grant = runtime.permissions.grant("session", session, "uv run pytest -q")
    assert "passed" in runtime.check(session, "uv run pytest -q", DenyApprover())
    assert "REFUSÉE" in runtime.check(other, "uv run pytest -q", DenyApprover())
    assert "REFUSÉE" in runtime.check(session, "uv run pytest -q -x", DenyApprover())
    runtime.permissions.revoke(grant)
    assert "REFUSÉE" in runtime.check(session, "uv run pytest -q", DenyApprover())
    grant = runtime.permissions.grant("session", session, "pwd")
    with runtime.database.connect() as db:
        db.execute("UPDATE command_grants SET expires_at='2000-01-01' WHERE id=?",
                   (grant,))
    assert "REFUSÉE" in runtime.check(session, "pwd", DenyApprover())
    assert runner.call_count == 1
    with pytest.raises(ValueError):
        runtime.permissions.grant("session", session, "uv run pytest; ls")


def test_schedule_grants_copy_only_to_generated_tasks(runtime):
    """Inherit live schedule grants only through recorded task origins and honor
    revocation.
    """
    schedule = runtime.tasks.schedule("Tests", "* * * * *")
    grant = runtime.permissions.grant("schedule", schedule, "uv run pytest", hours=24)
    task = runtime.tasks.enqueue_due(datetime.now(UTC) + timedelta(minutes=2))[0]
    assert runtime.permissions.allows([("task", task)], "uv run pytest")
    assert not runtime.permissions.allows([("task", "unrelated")], "uv run pytest")
    runtime.permissions.revoke(grant)
    assert not runtime.permissions.allows([("task", task)], "uv run pytest")


def test_verification_freshness_detects_command_and_user_edits(runtime, monkeypatch):
    """Mark a successful check stale after the checked project content changes."""
    path = runtime.settings.workspace / "source.py"
    path.write_text("before")
    monkeypatch.setattr(runtime.runner, "run", lambda *args: "EXIT CODE: 0\nok")
    session = runtime.sessions.create()
    check = runtime.verifications(session)
    check.run_check("pwd")
    assert check.list()[0]["freshness"] == "current"
    path.write_text("after")
    assert check.list()[0]["freshness"] == "stale"


def test_failed_check_is_repaired_and_rechecked_with_fixed_criteria(settings):
    """Repair a failed task and rerun the same checks before emitting a final event."""
    llm = FakeLLM([response("Fini"), response(tools=[("write_file", {
        "path": "calc.py", "content": "fixed"})]), response("Corrigé")])
    with Runtime(replace(settings, repair_attempts=2), llm) as runtime:
        calls = []

        def run(arguments, timeout):
            """Capture check arguments and pass only after the scripted repair creates
            calc.py.
            """
            calls.append(arguments)
            ok = (settings.workspace / "calc.py").exists()
            return "EXIT CODE: 0\npassed" if ok else "EXIT CODE: 1\nwrong answer"

        runtime.runner.run = run
        task = runtime.tasks.create("Corrige", ["uv run pytest"])
        runtime.permissions.grant("task", task, "uv run pytest")
        events = []
        result = runtime.run_task(task, emit=events.append)
        assert result.verification == "passed" and result.status == "completed"
        assert calls == [["uv", "run", "pytest"]] * 2
        assert len([e for e in events if e["type"] == "repair"]) == 1
        assert len([e for e in events if e["type"] == "final"]) == 1


def test_repairs_stop_when_no_progress_and_share_step_budget(settings):
    """Bound repair iterations by unchanged failures and the original model-step budget.
    """
    with Runtime(replace(settings, repair_attempts=5, max_agent_steps=2),
                 FakeLLM([response(), response()])) as runtime:
        runtime.runner.run = Mock(return_value="EXIT CODE: 1\nfailure")
        task = runtime.tasks.create("Corrige", ["pwd"])
        runtime.permissions.grant("task", task, "pwd")
        result = runtime.run_task(task)
        assert result.status == "failed"
        assert len(runtime.llm.calls) == 2


def test_preview_partial_accept_and_selective_undo(runtime):
    """Apply or undo chosen hunks while leaving other file regions unchanged."""
    session = runtime.sessions.create()
    path = runtime.settings.workspace / "source"
    path.write_text("a\nmiddle\nz\n")
    store = ChangeStore(runtime.database, session, runtime.settings.workspace, True)
    with pytest.raises(ValueError, match="préparée"):
        store.write(path, "A\nmiddle\nZ\n")
    assert path.read_text() == "a\nmiddle\nz\n"
    proposal = store.list()[0]["id"]
    hunks = store.hunks(proposal)
    store.review(proposal, "accept", [hunks[0]["index"]])
    assert path.read_text() == "A\nmiddle\nz\n"
    store = runtime.changes(session)
    store.write(path, "AA\nmiddle\nZZ\n")
    edit = store.list()[0]["id"]
    store.review(edit, "undo", [store.hunks(edit)[0]["index"]])
    assert path.read_text() == "A\nmiddle\nZZ\n"


def test_review_refuses_stale_proposals(runtime):
    """Refuse a stale proposal while still allowing rejection without changing the file.
    """
    session = runtime.sessions.create()
    path = runtime.settings.workspace / "x"
    path.write_text("old")
    store = ChangeStore(runtime.database, session, runtime.settings.workspace, True)
    with pytest.raises(ValueError):
        store.write(path, "new")
    proposal = store.list()[0]["id"]
    path.write_text("user")
    with pytest.raises(ValueError, match="préserver"):
        store.review(proposal, "accept")
    store.review(proposal, "reject")
    assert path.read_text() == "user"


def test_session_titles_search_fork_export_and_model_choice(runtime):
    """Copy conversation metadata into a fork without inheriting edits or permissions.
    """
    session = runtime.sessions.create()
    runtime.sessions.save(session, {"role": "user", "content": "Bug CSV"})
    runtime.sessions.rename(session, "Mon parseur")
    assert runtime.sessions.list("parseur")[0]["id"] == session
    assert runtime.sessions.list("CSV")[0]["id"] == session
    runtime.sessions.set_model(session, "another")
    fork = runtime.sessions.fork(session)
    assert runtime.sessions.model(fork) == "another"
    assert "Bug CSV" in runtime.sessions.export(fork)
    assert not runtime.permissions.allows([("session", fork)], "pwd")
    assert runtime.changes(fork).list() == []


def test_model_selection_validates_installed_model_and_applies_per_session(runtime):
    """Reject missing models and apply each session's model or the configured default.
    """
    runtime.llm.model = "test"
    runtime.llm.client = Mock()
    runtime.llm.client.list.return_value.models = [Mock(model="local-new")]
    session = runtime.sessions.create()
    runtime.select_model(session, "local-new")
    with pytest.raises(ValueError, match="absent"):
        runtime.select_model(session, "missing")
    runtime.llm.responses = [response(), response()]
    runtime.run(session, "Hi")
    assert runtime.llm.model == "local-new"
    runtime.run(runtime.sessions.create(), "Hi")
    assert runtime.llm.model == "test"


def test_live_work_view_drives_real_worker_without_blocking_input(runtime):
    """Run a background model turn through the live UI with a simulated terminal."""
    runtime.llm.responses = [response("Bonjour")]
    session = runtime.sessions.create()
    with create_pipe_input() as pipe, create_app_session(input=pipe,
                                                        output=DummyOutput()):
        view = WorkView(runtime, session, "Salut")
        result = asyncio.run(view.run())
    assert result.status == "completed"


def test_live_work_view_approval_cancel_unblocks_worker(runtime):
    """Cancel a pending live approval and ensure its worker releases the runtime lock.
    """
    runtime.llm.responses = [response(tools=[("run_command", {"command": "pwd"})])]
    session = runtime.sessions.create()
    with create_pipe_input() as pipe, create_app_session(input=pipe,
                                                        output=DummyOutput()):
        view = WorkView(runtime, session, "Exécute pwd")
        original = view.request

        def request(decision):
            """Display the approval request and immediately simulate the user's stop
            command.
            """
            original(decision)
            view.submit("/stop")

        view.request = request
        with pytest.raises(RunCancelled):
            asyncio.run(view.run())
    assert not runtime.lock.locked()


def test_v4_commands_and_export_do_not_overwrite_user_files(runtime):
    """Exercise local metadata and review commands while preventing unsafe exports."""
    terminal = TerminalChat(runtime, plain=True, console=Console(file=io.StringIO()))
    terminal.command("/rename Exemple")
    terminal.command("/allow pwd")
    terminal.command("/permissions")
    terminal.command("/export chat.md")
    with pytest.raises(FileExistsError):
        terminal.command("/export chat.md")
    terminal.command("/review on")
    assert terminal.session in runtime.review_sessions
    with pytest.raises(PermissionError):
        terminal.command("/export ../outside.md")


def test_invalid_tool_can_recover_in_same_turn(runtime):
    """Clear a tool validation failure after a corrected call succeeds in the same turn.
    """
    (runtime.settings.workspace / "x").write_text("ok")
    runtime.llm.responses = [response(tools=[("read_file", {"path": "x",
                                  "start_line": "invalid"})]),
                             response(tools=[("read_file", {"path": "x"})]), response()]
    assert runtime.run(runtime.sessions.create(), "Lis x").status == "completed"


def test_read_file_preserves_content_without_navigation_characters(runtime):
    """Keep whitespace and CRLF content exact and separate from excerpt metadata."""
    path = runtime.settings.workspace / "exact.txt"
    path.write_bytes(b"  padded  \r\nlast\t")
    output = runtime.registry(runtime.sessions.create()).execute("read_file",
                                                                 {"path": "exact.txt"})
    assert json.loads(output)["content"] == "  padded  \r\nlast\t"


def test_fork_repairs_interrupted_tool_sequence(runtime):
    """Add an unknown-result placeholder when forking an unanswered tool call."""
    session = runtime.sessions.create()
    runtime.sessions.save(session, {"role": "user", "content": "Lis x"})
    runtime.sessions.save(session, response(tools=[("read_file", {"path": "x"})])
                          .message.model_dump(exclude_none=True))
    fork = runtime.sessions.fork(session)
    history = runtime.sessions.history(fork)
    assert history[2]["role"] == "tool" and "inconnu" in history[2]["content"]


def test_review_mode_refuses_processes_even_with_session_permission(runtime):
    """Apply review-mode execution restrictions before consulting saved command grants.
    """
    session = runtime.sessions.create()
    runtime.review_sessions.add(session)
    runtime.permissions.grant("session", session, "pwd")
    assert "REFUSÉE" in runtime.check(session, "pwd", DenyApprover())

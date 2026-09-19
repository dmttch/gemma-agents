"""Regression tests for checkpoints, compaction, edit recovery, checks, and terminal UI.
"""

import io
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
import rich_click as click
from click.testing import CliRunner
from conftest import FakeLLM, response
from prompt_toolkit import PromptSession
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from gemma_agents.agent.context import ContextBuilder
from gemma_agents.changes import patch_text
from gemma_agents.checkpoints import Checkpoints
from gemma_agents.llm.ollama import OllamaLLM
from gemma_agents.main import main
from gemma_agents.runtime import Runtime
from gemma_agents.terminal import (
    CommandCompleter,
    TerminalApproval,
    TerminalChat,
    TerminalRenderer,
    input_bindings,
    safe_text,
)


class Allow:
    """Approval double that permits every tool call in explicitly scoped tests."""

    def confirm(self, *args):
        """Approve the requested call without terminal interaction."""
        return True


def test_checkpoint_survives_restart_and_updates_during_turn(settings):
    """Refresh checkpoints in model context during a turn and after restarting runtime.
    """
    llm = FakeLLM([
        response(tools=[("save_checkpoint", {"objective": "Réparer le parseur",
                        "decisions": ["Préserver UTF-8"], "next_steps": ["Tester"]})]),
        response("Suite"),
    ])
    with Runtime(settings, llm) as runtime:
        session = runtime.sessions.create()
        runtime.run(session, "Travaille")
        assert "Préserver UTF-8" in llm.calls[1][0]["content"]
    with Runtime(settings, FakeLLM([response()])) as runtime:
        runtime.run(session, "Reprends")
        assert "Préserver UTF-8" in runtime.llm.calls[0][0]["content"]
        assert "Tester" in Checkpoints(runtime.database, session).get_checkpoint()


def test_compaction_bounds_context_and_preserves_full_history(runtime, tmp_path):
    """Condense old tool batches without losing the request, latest batch, or stored
    history.
    """
    session = runtime.sessions.create()
    prompt = tmp_path / "system.md"
    prompt.write_text("Agent")
    history = [{"role": "user", "content": "Objectif initial"}]
    for _ in range(8):
        history.extend([
            response(tools=[("read_file", {"path": "x"})])
            .message.model_dump(exclude_none=True),
            {"role": "tool", "tool_name": "read_file", "content": "x" * 10000},
        ])
    for message in history:
        runtime.sessions.save(session, message)
    events = []
    context = ContextBuilder(prompt, runtime.settings.workspace, 4000,
                             database=runtime.database, session_id=session,
                             emit=events.append)
    messages = context.build(history)
    assert len(json.dumps(messages, ensure_ascii=False)) <= 4000
    assert messages[1]["content"] == "Objectif initial"
    assert messages[-2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[-1]["role"] == "tool"
    assert events[-1]["compacted"] > 0
    assert runtime.sessions.history(session)[-1]["content"] == "x" * 10000
    with runtime.database.connect() as db:
        assert db.execute("SELECT content FROM context_notes").fetchone()


def test_edits_undo_chain_and_user_conflict(runtime):
    """Refuse undo over user changes and restore successive edits in reverse order."""
    session = runtime.sessions.create()
    files = runtime.registry(session)
    files.execute("write_file", {"path": "x.txt", "content": "alpha\n"})
    files.execute("apply_patch", {"path": "x.txt", "patch":
                  "--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-alpha\n+beta\n"})
    path = runtime.settings.workspace / "x.txt"
    assert path.read_text() == "beta\n"
    changes = runtime.changes(session)
    assert "-alpha" in changes.diff() and "+beta" in changes.diff()
    path.write_text("modification utilisateur")
    with pytest.raises(ValueError, match="préserver"):
        changes.undo()
    assert path.read_text() == "modification utilisateur"
    path.write_text("beta\n")
    changes.undo()
    assert path.read_text() == "alpha\n"
    changes.undo()
    assert not path.exists()


def test_undo_refuses_replaced_symlink_and_other_sessions(runtime):
    """Reject undo from another session or through a path replaced by a symlink."""
    session = runtime.sessions.create()
    files = runtime.registry(session)
    files.execute("write_file", {"path": "x", "content": "agent"})
    other = runtime.sessions.create()
    with pytest.raises(ValueError):
        runtime.changes(other).undo()
    path = runtime.settings.workspace / "x"
    path.unlink()
    target = runtime.settings.workspace / "user.txt"
    target.write_text("agent")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="Chemin"):
        runtime.changes(session).undo()
    assert target.read_text() == "agent"


@pytest.mark.parametrize("patch", [
    "--- a/../secret\n+++ b/../secret\n@@ -1 +1 @@\n-a\n+b\n",
    "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-wrong\n+b\n",
    "--- a/x\n+++ b/x\n@@ -1,2 +1 @@\n-a\n+b\n",
    "--- a/x\n+++ b/x\n@@ -1 +5 @@\n-a\n+b\n",
])
def test_patch_rejects_path_context_and_count_mismatch(patch):
    """Reject diffs with mismatched paths, source text, line counts, or destinations."""
    with pytest.raises(ValueError):
        patch_text("a\n", patch, "x")


def test_patch_multiple_hunks_and_missing_newline():
    """Apply separated hunks while preserving the absence of a final newline."""
    patch = ("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+A\n"
             "@@ -3 +3 @@\n-c\n\\ No newline at end of file\n"
             "+C\n\\ No newline at end of file\n")
    assert patch_text("a\nb\nc", patch, "x") == "A\nb\nC"


def test_bounded_reads(runtime):
    """Read only the requested line range from a large file with a bounded response."""
    path = runtime.settings.workspace / "large"
    path.write_text("\n".join(f"ligne {i}" for i in range(100000)))
    registry = runtime.registry(runtime.sessions.create())
    result = registry.execute("read_file", {"path": "large", "start_line": 501,
                                           "end_line": 502})
    assert "ligne 500" in result and "ligne 501" in result
    assert "ligne 502" not in result and len(result) < 200


def test_task_resume_keeps_session_and_repairs_interrupted_calls(runtime):
    """Resume a failed task in its original session without repeating an applied edit.
    """
    task = runtime.tasks.create("Créer un fichier")
    runtime.llm.responses = [response(tools=[("write_file", {"path": "x",
                                                           "content": "done"})])]
    with pytest.raises(IndexError):
        runtime.run_task(task)
    session = runtime.tasks.get(task)["session_id"]
    runtime.tasks.recover(task)
    runtime.llm.responses = [response("Reprise terminée")]
    result = runtime.run_task(task)
    assert result.session_id == session
    assert "done" in json.dumps(runtime.llm.calls[-1])
    assert len(runtime.changes(session).list()) == 1


@pytest.mark.parametrize("output, status, verification", [
    ("EXIT CODE: 0\n3 passed", "completed", "passed"),
    ("EXIT CODE: 1\n1 failed", "failed", "failed"),
    ("TIMEOUT après 1s.", "failed", "failed"),
])
def test_task_criteria_override_model_claim(runtime, monkeypatch,
                                           output, status, verification):
    """Derive task status from observed check output and emit exactly one final result.
    """
    monkeypatch.setattr(runtime.runner, "run", lambda *args: output)
    task = runtime.tasks.create("Corrige", ["uv run pytest -q"])
    runtime.llm.responses = [response("Tout est réussi !")]
    events = []
    result = runtime.run_task(task, Allow(), events.append)
    assert (result.status, result.verification) == (status, verification)
    assert runtime.tasks.get(task)["status"] == status
    assert events[-1]["status"] == status
    assert sum(event["type"] == "final" for event in events) == 1
    records = runtime.verifications(result.session_id).list()
    assert records[0]["status"] == verification


def test_worker_cannot_bypass_checks_or_command_policy(runtime, monkeypatch):
    """Block checks without approval and reject shell constructs even with an approver.
    """
    runner = Mock(return_value="EXIT CODE: 0\n")
    monkeypatch.setattr(runtime.runner, "run", runner)
    task = runtime.tasks.create("Corrige", ["uv run pytest"])
    runtime.llm.responses = [response()]
    assert runtime.run_task(task).status == "blocked"
    session = runtime.tasks.get(task)["session_id"]
    assert "REFUSÉE" in runtime.check(session, "uv run pytest; ls", Allow())
    runner.assert_not_called()


def test_keyboard_interrupt_persists_and_allows_next_turn(settings):
    """Persist interruption and release the runtime lock so explicit recovery can
    proceed.
    """
    class InterruptedLLM(FakeLLM):
        """Model double that interrupts generation before returning any response."""

        def chat(self, messages, tools):
            """Raise KeyboardInterrupt to exercise task and run interruption cleanup."""
            raise KeyboardInterrupt

    with Runtime(settings, InterruptedLLM()) as runtime:
        task = runtime.tasks.create("Inspecte")
        with pytest.raises(KeyboardInterrupt):
            runtime.run_task(task)
        assert runtime.tasks.get(task)["status"] == "interrupted"
        with runtime.database.connect() as db:
            assert db.execute("SELECT status FROM runs").fetchone()[0] == "interrupted"
        assert not runtime.lock.locked()
        runtime.tasks.recover(task)
        runtime.llm = FakeLLM([response()])
        assert runtime.run_task(task).status == "completed"


def test_stream_preserves_tool_calls_and_closes_on_interrupt():
    """Accumulate streamed text/tools and close the generator when emission is
    interrupted.
    """
    llm = OllamaLLM("test", "http://localhost:11434")
    closed = []

    def chunks():
        """Yield text and a tool call, recording generator closure on every exit path.
        """
        try:
            yield response("Bon")
            yield response("jour", [("list_files", {})])
        finally:
            closed.append(True)

    llm.client.chat = Mock(side_effect=lambda **kwargs: chunks())
    events = []
    result = llm.chat_stream([], [], events.append)
    assert result.message.content == "Bonjour"
    assert result.message.tool_calls[0].function.name == "list_files"
    assert len(events) == 2 and closed == [True]

    def interrupt(event):
        """Abort token handling to verify stream cleanup on callback failure."""
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        llm.chat_stream([], [], interrupt)
    assert closed == [True, True]
    llm.close()


def test_terminal_completion_and_multiline_paste(runtime):
    """Complete local commands and preserve explicit newlines and bracketed pasted text.
    """
    completer = CommandCompleter(runtime)
    assert "/checkpoint" in [c.text for c in completer.get_completions(
        Document("/ch"), None)]
    with create_pipe_input() as pipe:
        editor = PromptSession(input=pipe, output=DummyOutput(), multiline=True,
                               key_bindings=input_bindings())
        pipe.send_text("première\x1b\rseconde\r")
        assert editor.prompt() == "première\nseconde"
        pipe.send_text("\x1b[200~collé\nmultiligne\x1b[201~\r")
        assert editor.prompt() == "collé\nmultiligne"


def test_terminal_commands_do_not_call_model_and_preserve_workspace(runtime):
    """Run local session commands without model calls and restore the selected session.
    """
    output = io.StringIO()
    terminal = TerminalChat(runtime, plain=True,
                            console=Console(file=output, width=80))
    old = terminal.session
    for command in ["/help", "/plan", "/checks", "/diff", "/new", "/sessions"]:
        terminal.command(command)
    assert terminal.session != old
    terminal.command("/resume " + old)
    assert terminal.session == old
    assert runtime.llm.calls == []
    assert "Commandes" in output.getvalue()


def test_plain_cli_chat_and_commands(settings, monkeypatch):
    """Exercise plain chat, local commands, and the persisted-session exit message."""
    runtime = Runtime(settings, FakeLLM([response("**Bonjour**")]))
    monkeypatch.setattr("gemma_agents.main.Runtime", lambda settings: runtime)
    monkeypatch.setattr("gemma_agents.main.Settings.from_env", lambda: settings)
    result = CliRunner().invoke(main, ["--plain"],
                                input="/help\nBonjour\n/plan\n/quit\n")
    assert result.exit_code == 0, result.output
    assert "V4" in result.output and "Bonjour" in result.output
    assert "completed" in result.output
    assert "reprise" in result.output


def test_terminal_narrow_output_and_control_sequences():
    """Preserve literal markup while stripping terminal controls from narrow output."""
    output = io.StringIO()
    renderer = TerminalRenderer(Console(file=output, width=42, color_system=None),
                                False)
    renderer.begin()
    renderer({"type": "tool_request", "name": "read_file",
              "arguments": {"path": "[red]x\x1b[2J"}})
    assert "\x1b" not in output.getvalue()
    assert "[red]" in output.getvalue()
    assert safe_text("ok\x1b\x07\x9b") == "ok"


def test_task_criteria_persist_across_runtime_restart(settings):
    """Reload a task's saved verification commands after recreating the runtime."""
    with Runtime(settings, FakeLLM()) as runtime:
        task = runtime.tasks.create("Teste", ["uv run pytest"])
    with Runtime(replace(settings), FakeLLM()) as runtime:
        assert runtime.tasks.checks(task) == ["uv run pytest"]


def test_terminal_check_stops_activity_and_records_observation(runtime, monkeypatch):
    """Pause rendering around approval and persist the observed verification outcome."""
    terminal = TerminalChat(runtime, plain=True,
                            console=Console(file=io.StringIO()))
    monkeypatch.setattr(runtime.runner, "run", lambda *args: "EXIT CODE: 0\nok")
    monkeypatch.setattr("gemma_agents.terminal.click.confirm", lambda *a, **kw: True)
    stopped = Mock()
    monkeypatch.setattr(terminal.renderer, "pause", stopped)
    terminal.command("/check pwd")
    assert stopped.call_count >= 2
    assert "VÉRIFICATION : passed" in runtime.sessions.history(terminal.session)[-1][
        "content"]


def test_terminal_undo_updates_conversation(runtime):
    """Record the user's undo and its result after removing a session-created file."""
    terminal = TerminalChat(runtime, plain=True,
                            console=Console(file=io.StringIO()))
    runtime.registry(terminal.session).execute("write_file", {"path": "x",
                                                              "content": "created"})
    terminal.command("/undo")
    assert not (runtime.settings.workspace / "x").exists()
    assert "annulée" in runtime.sessions.history(terminal.session)[-1]["content"]


def test_ctrl_c_in_terminal_approval_interrupts_turn(monkeypatch):
    """Propagate an aborted approval prompt as KeyboardInterrupt to stop the turn."""
    renderer = TerminalRenderer(Console(file=io.StringIO()), False)
    monkeypatch.setattr("gemma_agents.terminal.click.confirm", Mock(
        side_effect=click.Abort))
    with pytest.raises(KeyboardInterrupt):
        TerminalApproval(renderer).confirm("run_command", {"command": "pwd"}, "Test")

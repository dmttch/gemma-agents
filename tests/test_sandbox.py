"""Test real process isolation, output limits, timeout handling, and cleanup."""

import sys
import time

import pytest

from gemma_agents.security.sandbox import SandboxRunner
from gemma_agents.tools.processes import ProcessTools


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt integration")
def test_real_sandbox_allows_workspace_denies_external_files_and_network(tmp_path):
    """Verify Seatbelt permits workspace writes but denies external access and sockets.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("private")
    script = workspace / "probe.py"
    script.write_text(
        "from pathlib import Path\nimport socket\n"
        "Path('inside').write_text('ok')\n"
        f"secret = Path({str(secret)!r})\n"
        "try:\n secret.read_text()\n print('READ_ESCAPE')\n"
        "except PermissionError:\n print('READ_DENIED')\n"
        "try:\n secret.write_text('changed')\n print('WRITE_ESCAPE')\n"
        "except PermissionError:\n print('WRITE_DENIED')\n"
        "try:\n socket.socket().bind(('127.0.0.1', 0))\n print('NETWORK_ESCAPE')\n"
        "except PermissionError:\n print('NETWORK_DENIED')\n"
    )
    runner = SandboxRunner(workspace)
    try:
        output = runner.run([sys.executable, str(script)], timeout=15)
        assert "EXIT CODE: 0" in output, output
        assert "READ_DENIED" in output
        assert "WRITE_DENIED" in output
        assert "NETWORK_DENIED" in output
        assert (workspace / "inside").read_text() == "ok"
        assert secret.read_text() == "private"
    finally:
        runner.close()


def test_missing_sandbox_never_falls_back(tmp_path, monkeypatch):
    """Refuse execution when required sandbox support is unavailable."""
    runner = SandboxRunner(tmp_path)
    monkeypatch.setattr(SandboxRunner, "available", property(lambda self: False))
    try:
        with pytest.raises(RuntimeError, match="refusée"):
            runner.run(["pwd"])
    finally:
        runner.close()


def test_background_output_cap_timeout_and_cleanup(tmp_path):
    """Bound noisy process output, report timeouts, and clean up terminated jobs."""
    script = tmp_path / "process.py"
    script.write_text("import time\nprint('x'*100000, flush=True)\ntime.sleep(30)\n")
    runner = SandboxRunner(tmp_path, mode="off")
    processes = ProcessTools(runner)
    try:
        job_id = processes.start_process(f"{sys.executable} {script}")
        deadline = time.monotonic() + 5
        job = processes.jobs[job_id]
        while len(job.output) < job.MAX_OUTPUT and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(job.output) == job.MAX_OUTPUT
        assert "running" in processes.process_status(job_id)
        processes.stop_process(job_id)
        assert job.process.poll() is not None
        assert "TIMEOUT" in runner.run([sys.executable, str(script)], timeout=1)
    finally:
        runner.close()
    assert all(job.process.poll() is not None for job in processes.jobs.values())


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt integration")
def test_uv_and_git_work_inside_sandbox(tmp_path):
    """Exercise local uv execution and basic Git operations under the real sandbox."""
    from gemma_agents.tools.git import GitTools

    (tmp_path / "hello.py").write_text("print('UV_OK')\n")
    runner = SandboxRunner(tmp_path)
    try:
        output = runner.run(["uv", "run", "--no-project", "--python", sys.executable,
                             "hello.py"], timeout=15)
        assert "EXIT CODE: 0" in output, output
        assert "UV_OK" in output
        assert "EXIT CODE: 0" in runner.run(["git", "init"], timeout=15)
        (tmp_path / "file.txt").write_text("text")
        git = GitTools(tmp_path, runner)
        assert "file.txt" in git.git_status()
        assert "EXIT CODE: 0" in git.git_add("file.txt")
        assert "+text" in git.git_diff(staged=True)
    finally:
        runner.close()


def test_stopping_finished_process_never_signals_reused_pid(tmp_path, monkeypatch):
    """Ensure repeated cleanup never signals a process group after it was reaped."""
    import os

    runner = SandboxRunner(tmp_path, mode="off")
    job = runner.start(["pwd"])
    job.reaper.join(timeout=3)
    assert job.cleaned
    signals = []
    monkeypatch.setattr(os, "killpg", lambda *args: signals.append(args))
    job.stop()
    runner.close()
    assert signals == []


def test_background_descendants_stop_when_parent_exits(tmp_path):
    """Kill orphaned descendants before they can perform a delayed workspace write."""
    script = tmp_path / "fork.py"
    script.write_text(
        "import os, time\nfrom pathlib import Path\n"
        "if os.fork() == 0:\n time.sleep(1)\n Path('escaped').touch()\n"
    )
    runner = SandboxRunner(tmp_path, mode="off")
    try:
        job = runner.start([sys.executable, str(script)])
        job.reaper.join(timeout=3)
        assert job.cleaned
        time.sleep(1.1)
        assert not (tmp_path / "escaped").exists()
    finally:
        runner.close()

"""Best-effort macOS Seatbelt adapter. Never silently runs unsandboxed."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


class ManagedProcess:
    """Own a process group, bounded output buffer, and asynchronous cleanup threads."""

    MAX_OUTPUT = 40_000

    def __init__(self, process: subprocess.Popen):
        """Start output collection and process reaping for an already launched child."""
        self.process = process
        self.output = bytearray()
        self.lock = threading.Lock()
        self.cleanup_lock = threading.Lock()
        self.cleaned = False
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.reaper = threading.Thread(target=self._reap, daemon=True)
        self.reaper.start()

    def _read(self):
        """Drain stdout continuously, retaining only the most recent MAX_OUTPUT bytes.
        """
        try:
            while chunk := self.process.stdout.read(4096):
                with self.lock:
                    self.output.extend(chunk)
                    # Keep draining the pipe even after the display buffer fills,
                    # otherwise a noisy child could block on stdout indefinitely.
                    del self.output[:-self.MAX_OUTPUT]
        finally:
            self.process.stdout.close()

    def text(self) -> str:
        """Decode a locked snapshot of the output tail, replacing incomplete UTF-8
        bytes.
        """
        with self.lock:
            return bytes(self.output).decode("utf-8", errors="replace")

    def _kill_group(self) -> None:
        """Kill the owned process group at most once under a cleanup lock."""
        with self.cleanup_lock:
            if self.cleaned:
                return
            # Clean descendants immediately; never signal an old, reusable PID later.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.cleaned = True

    def _reap(self) -> None:
        """Wait for the parent, stop surviving descendants, and allow output draining.
        """
        self.process.wait()
        self._kill_group()
        self.reader.join(timeout=2)

    def stop(self) -> None:
        """Stop the process group and wait for parent and reaper cleanup."""
        self._kill_group()
        self.process.wait()
        self.reaper.join(timeout=3)


class SandboxRunner:
    """Launch isolated commands and own their process groups and scratch directory."""

    def __init__(self, workspace: Path, mode: str = "required"):
        """Validate the mode and create private scratch storage for managed processes.
        """
        if mode not in {"required", "off"}:
            raise ValueError("Mode sandbox inconnu.")
        self.workspace = workspace.resolve()
        self.mode = mode
        self._temp = tempfile.TemporaryDirectory(prefix="gemma-sandbox-")
        self.scratch = Path(self._temp.name).resolve()
        self.processes: list[ManagedProcess] = []
        self.cancel_event = None
        # Capture operator tool locations before project code can run. Never
        # resolve a tool from the workspace, even when it appears on PATH.
        roots = [str(Path.home() / ".local/bin"), "/opt/homebrew/bin",
                 "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        roots.extend(os.get_exec_path())
        self.tool_path = ":".join(dict.fromkeys(
            str(Path(root).resolve()) for root in roots if root
            and not Path(root).resolve().is_relative_to(self.workspace)))
        self.executables = {}
        for name in ("uv", "git", "ls", "pwd", "head", "tail"):
            found = shutil.which(name, path=self.tool_path)
            if found and not Path(found).resolve().is_relative_to(self.workspace):
                self.executables[name] = str(Path(found).resolve())

    def executable(self, name: str) -> str:
        """Return a pinned operator executable, refusing workspace executables."""
        candidate = self.executables.get(name)
        if candidate is None and Path(name).is_absolute():
            candidate = str(Path(name).resolve())
        if (not candidate or Path(candidate).is_relative_to(self.workspace)
                or not os.access(candidate, os.X_OK)):
            raise FileNotFoundError(f"Programme introuvable ou non fiable : {name}")
        return candidate

    @property
    def available(self) -> bool:
        """Report whether the macOS sandbox-exec binary is present."""
        return sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists()

    def profile(self) -> str:
        """Build a default-deny Seatbelt profile with bounded read and write roots."""
        read_roots = {
            "/System", "/usr", "/bin", "/sbin", "/Library", "/opt/homebrew",
            str(Path(sys.base_prefix).resolve()), str(Path(sys.prefix).resolve()),
            str(self.workspace), str(self.scratch), "/private/var/db/dyld",
        }
        lines = [
            "(version 1)", "(deny default)", "(allow process*)",
            "(allow sysctl-read)", "(allow mach-lookup)",
            "(allow file-read-metadata)",
            # macOS dyld opens the root directory while selecting its shared cache.
            '(allow file-read-data (literal "/"))',
            '(allow file-read* (literal "/dev/null") (literal "/dev/urandom"))',
            '(allow file-write* (literal "/dev/null"))',
        ]
        for root in sorted(read_roots):
            lines.append(f"(allow file-read* (subpath {json.dumps(root)}))")
        for executable in self.executables.values():
            lines.append(f"(allow file-read* (literal {json.dumps(executable)}))")
        for root in (self.workspace, self.scratch):
            lines.append(f"(allow file-write* (subpath {json.dumps(str(root))}))")
        return "\n".join(lines)

    def start(self, arguments: list[str]) -> ManagedProcess:
        """Launch an argument vector with a minimal environment and no shell.

        In required mode, refuse execution when Seatbelt is unavailable. Resolve
        the executable through captured operator locations and create a separate process
        group so cleanup can stop descendants as well as their parent.
        """
        if not arguments:
            raise ValueError("Commande vide.")
        if self.mode == "required" and not self.available:
            raise RuntimeError("Sandbox macOS indisponible : exécution refusée.")
        executable = self.executable(arguments[0])
        command = [executable, *arguments[1:]]
        if self.mode == "required":
            command = ["/usr/bin/sandbox-exec", "-p", self.profile(), *command]
        # Build a minimal environment instead of inheriting credentials, user
        # configuration, or caches. Redirect child writes into private scratch.
        environment = {
            "PATH": self.tool_path,
            "HOME": str(self.scratch),
            "TMPDIR": str(self.scratch),
            "UV_CACHE_DIR": str(self.scratch / "uv-cache"),
            "UV_PYTHON_INSTALL_DIR": str(Path(sys.base_prefix).parent),
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_OFFLINE": "1",
            "LANG": "en_US.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
        }
        process = subprocess.Popen(
            command, cwd=self.workspace, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            # A separate process group lets cleanup stop descendants as well.
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        managed = ManagedProcess(process)
        self.processes.append(managed)
        return managed

    def run(self, arguments: list[str], timeout: int = 120) -> str:
        """Wait for a command with timeout and cancellation, then stop its process
        group.

        Return an exit-code or timeout prefix with bounded captured output.
        Cancellation propagates as RunCancelled after cleanup.
        """
        managed = self.start(arguments)
        try:
            deadline = time.monotonic() + timeout
            while managed.process.poll() is None:
                if self.cancel_event and self.cancel_event.is_set():
                    from gemma_agents.control import RunCancelled
                    raise RunCancelled("Commande annulée.")
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(arguments, timeout)
                try:
                    managed.process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            code = managed.process.returncode
            managed.reader.join(timeout=1)
            return f"EXIT CODE: {code}\n{managed.text()}"
        except subprocess.TimeoutExpired:
            return f"TIMEOUT après {timeout}s.\n{managed.text()}"
        finally:
            managed.stop()
            self.processes.remove(managed)

    def close(self) -> None:
        """Stop all remaining processes and remove the private scratch directory."""
        for managed in self.processes:
            managed.stop()
        self.processes.clear()
        self._temp.cleanup()

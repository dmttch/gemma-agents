"""Execute approved argument vectors in the workspace without invoking a shell."""

import shlex
from pathlib import Path

from gemma_agents.security.sandbox import SandboxRunner


class ShellTools:
    """Adapt approved command strings to timed sandbox process execution."""

    def __init__(self, workspace: Path, timeout: int = 120,
                 runner: SandboxRunner | None = None):
        """Retain a supplied sandbox runner or create one with the configured timeout.
        """
        self.runner = runner or SandboxRunner(workspace)
        self.timeout = timeout

    def run_command(self, command: str) -> str:
        """Run an approved command in the workspace sandbox, without a shell.

        Args:
            command: Program and arguments, e.g. uv run pytest.
        """
        return self.runner.run(shlex.split(command), self.timeout)

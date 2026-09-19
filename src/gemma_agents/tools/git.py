"""Expose constrained Git inspection and mutation through the sandbox runner."""

import shlex
from pathlib import Path

from gemma_agents.security.sandbox import SandboxRunner


class GitTools:
    """Run workspace Git operations with hooks and external diff helpers disabled."""

    def __init__(self, workspace: Path, runner: SandboxRunner | None = None):
        """Resolve the workspace and reuse or create a sandbox runner for Git commands.
        """
        self.workspace = workspace.resolve()
        self.runner = runner or SandboxRunner(workspace)

    def _run(self, arguments: list[str]) -> str:
        """Run Git with execution hooks disabled and a 60-second command timeout."""

        # Repository configuration must not launch hooks, file monitors, or
        # signing helpers as a side effect of these controlled Git operations.
        return self.runner.run([
            "git", "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false", "-c", "commit.gpgSign=false",
            "-C", str(self.workspace), *arguments,
        ], timeout=60)

    def git_status(self) -> str:
        """
        Show the current Git repository status.

        Returns:
            Git status.
        """

        return self._run(
            ["status", "--short"]
        )

    def git_diff(self, staged: bool = False) -> str:
        """
        Show Git changes.

        Args:
            staged: Show staged changes instead of unstaged changes.

        Returns:
            Git diff.
        """

        arguments = ["diff", "--no-ext-diff", "--no-textconv"]

        if staged:
            arguments.append("--cached")

        return self._run(arguments)

    def git_log(self, limit: int = 10) -> str:
        """
        Show recent Git commits.

        Args:
            limit: Maximum number of commits.

        Returns:
            Recent commit history.
        """

        limit = max(1, min(limit, 50))

        return self._run(
            [
                "log",
                f"-{limit}",
                "--oneline",
                "--decorate",
            ]
        )

    def git_add(self, paths: str = ".") -> str:
        """
        Stage files for the next Git commit.

        Args:
            paths: Space-separated workspace paths to stage.

        Returns:
            Git result.
        """

        parsed = shlex.split(paths)

        if not parsed:
            parsed = ["."]

        for path in parsed:
            if path.startswith("-"):
                return (
                    "Refus : les options Git arbitraires "
                    "ne sont pas autorisées."
                )

        return self._run(
            ["add", "--", *parsed]
        )

    def git_commit(self, message: str) -> str:
        """
        Create a Git commit.

        Args:
            message: Git commit message.

        Returns:
            Git result.
        """

        if not message.strip():
            return "Message de commit vide."

        return self._run(
            [
                "commit",
                "-m",
                message.strip(),
            ]
        )

"""Install and exercise a built wheel outside the checkout using uv tool isolation."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import rich_click as click


@click.command()
@click.argument("wheel", type=click.Path(exists=True, path_type=Path))
def main(wheel):
    """Verify packaged CLI, resources and SQLite lifecycle without an Ollama server."""
    wheel = wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="gemma-wheel-") as folder:
        root = Path(folder)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("AGENT_") and key != "PYTHONPATH"}
        environment.update(UV_TOOL_DIR=str(root / "tools"),
                           UV_TOOL_BIN_DIR=str(root / "bin"),
                           AGENT_STORAGE=str(root / "storage"),
                           AGENT_WORKSPACE=str(root / "workspace"))

        def run(arguments):
            """Require successful subprocess completion and capture diagnostics."""
            result = subprocess.run(arguments, cwd=root, env=environment,
                                    capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise click.ClickException(result.stdout + result.stderr)
            return result.stdout

        run(["uv", "tool", "install", "--python", sys.executable, str(wheel)])
        executable = str(root / "bin/gemma-agents")
        assert "gemma-agents" in run([executable, "--version"])
        assert "doctor" in run([executable, "--help"])
        task = json.loads(run([executable, "--json", "tasks", "add", "Smoke task"]))
        tasks = json.loads(run([executable, "--json", "tasks", "list"]))
        assert tasks[0]["id"] == task
        backup = root / "backup.db"
        run([executable, "--json", "storage", "backup", str(backup)])
        run([executable, "--json", "storage", "restore", str(backup), "--yes"])
        installed_python = root / "tools/gemma-agents/bin/python"
        probe = root / "probe.py"
        probe.write_text("from importlib.resources import files\n"
                         "p = files('gemma_agents').joinpath('prompts/system.md')\n"
                         "assert len(p.read_text()) > 100\n")
        run(["uv", "run", "--no-project", "--python",
             str(installed_python), str(probe)])
    click.echo("Wheel installation, CLI, prompt resource and storage lifecycle passed.")


if __name__ == "__main__":
    main()

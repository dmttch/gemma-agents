"""Real-model V4 evaluations; generated test commands run in the runtime sandbox."""

import json
import shlex
import sys
import tempfile
import time
from pathlib import Path

import rich_click as click
from rich.console import Console

from gemma_agents.config import BASE_DIR, Settings
from gemma_agents.control import RunCancelled, RunControl
from gemma_agents.runtime import Runtime
from gemma_agents.security.approvals import DenyApprover
from gemma_agents.security.gateway import ToolGateway
from gemma_agents.security.policy import SecurityPolicy


def repair(runtime, emit):
    """Evaluate whether the model fixes an addition bug while preserving the test file.
    """
    root = runtime.settings.workspace
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    test = "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    (root / "test_calc.py").write_text(test)
    (root / "AGENTS.md").write_text("Ne modifie pas test_calc.py. Corrige calc.py.")
    command = shlex.join(["uv", "run", "--no-project", "--offline", "--python",
                          sys.executable, "-m", "pytest", "-q"])
    task = runtime.tasks.create("Corrige add dans calc.py. Préserve test_calc.py. "
                                "Le runtime exécutera les validations finales.",
                                [command])
    runtime.permissions.grant("task", task, command)
    result = runtime.run_task(task, emit=emit)
    return (result.verification == "passed" and
            (root / "test_calc.py").read_text() == test), result


def multiple_files(runtime, emit):
    """Evaluate coordinated JSON and text-file creation against exact expected content.
    """
    session = runtime.sessions.create()
    result = runtime.run(session, 'Crée config.json contenant {"port": 8080}, puis '
                          'README.txt contenant uniquement le texte "Port: 8080". '
                          "Vérifie les deux fichiers sans commande shell.", emit=emit)
    root = runtime.settings.workspace
    return (json.loads((root / "config.json").read_text()) == {"port": 8080}
            and (root / "README.txt").read_text().strip() == "Port: 8080"), result


def interrupted(runtime, emit):
    """Evaluate resume behavior after cancellation immediately following the first
    write.
    """
    session = runtime.sessions.create()
    control = RunControl()

    def first(event):
        """Forward progress and request cancellation after the first successful file
        write.
        """
        emit(event)
        if (event["type"] == "tool_result" and event["name"] == "write_file"
                and "Fichier écrit" in event["content"]):
            control.cancel()

    try:
        runtime.run(session, "Crée first.txt avec le texte ONE puis second.txt "
                    "avec le texte TWO, dans cet ordre, sans commande shell.",
                    emit=first, control=control)
    except RunCancelled:
        pass
    result = runtime.run(session, "Reprends après l'interruption. Inspecte les fichiers"
                          "déjà présents et termine la demande précédente.", emit=emit)
    root = runtime.settings.workspace
    edits = [e for e in runtime.changes(session).list() if e["path"] == "first.txt"]
    return (control.cancelled.is_set() and len(edits) == 1
            and (root / "first.txt").read_text().strip() == "ONE"
            and (root / "second.txt").read_text().strip() == "TWO"), result


def invalid_tool(runtime, emit):
    """Seed a rejected tool call and evaluate recovery through a corrected file read."""
    session = runtime.sessions.create()
    (runtime.settings.workspace / "data.txt").write_text("RECOVERED")
    args = {"path": "data.txt", "start_line": "invalid"}
    runtime.sessions.save(session, {"role": "user", "content": "Lis data.txt"})
    runtime.sessions.save(session, {"role": "assistant", "content": "",
                                    "tool_calls": [{"function": {"name": "read_file",
                                                                  "arguments": args}}]})
    gateway = ToolGateway(runtime.registry(session), SecurityPolicy(
        runtime.settings.workspace), DenyApprover(), runtime.database, session)
    error = gateway.execute("read_file", args)
    runtime.sessions.save(session, {"role": "tool", "tool_name": "read_file",
                                    "content": error})
    result = runtime.run(session, "Corrige cet appel invalide puis crée recovered.txt "
                          "contenant exactement le texte lu dans data.txt.", emit=emit)
    actual = (runtime.settings.workspace / "recovered.txt").read_text()
    return actual == "RECOVERED", result


@click.command()
@click.option("--model", default="gemma4:12b-mlx", show_default=True)
@click.option("--host", default="http://localhost:11434")
@click.option("--output", type=click.Path(path_type=Path))
@click.option("--scenario", type=click.Choice([
    "repair", "multiple_files", "interrupted", "invalid_tool"]), multiple=True)
def main(model, host, output, scenario):
    """Evaluate bug fixing, multiple files, interruption and invalid tools."""
    console = Console(stderr=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="gemma-v4-eval-") as directory:
        root = Path(directory)
        for case in [repair, multiple_files, interrupted, invalid_tool]:
            if scenario and case.__name__ not in scenario:
                continue
            events = []
            start = time.monotonic()
            console.print(f"Évaluation {case.__name__} · {model}", markup=False)
            settings = Settings(model=model, ollama_host=host,
                                workspace=root / case.__name__,
                                storage_dir=root / "storage",
                                database_path=root / "storage/agent.db",
                                system_prompt_path=BASE_DIR / "prompts/system.md")
            try:
                with Runtime(settings) as runtime:
                    passed, result = case(runtime, events.append)
                    passed = passed and result.status == "completed"
                    detail = "" if passed else result.content[:1000]
            except Exception as error:
                passed, detail = False, str(error)
            row = {"scenario": case.__name__, "passed": passed,
                   "seconds": round(time.monotonic() - start, 2),
                   "tool_calls": sum(e["type"] == "tool_request" for e in events),
                   "repairs": sum(e["type"] == "repair" for e in events),
                   "detail": detail}
            results.append(row)
            console.print(row)
    payload = json.dumps({"model": model, "results": results},
                          ensure_ascii=False, indent=2)
    if output:
        output.write_text(payload + "\n")
    click.echo(payload)
    if not all(r["passed"] for r in results):
        raise click.ClickException("Au moins une évaluation V4 a échoué.")


if __name__ == "__main__":
    main()

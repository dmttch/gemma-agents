"""Opt-in evaluations against a real local Ollama model in disposable workspaces."""

import json
import tempfile
import time
from pathlib import Path

import rich_click as click
from rich.console import Console

from gemma_agents.config import BASE_DIR, Settings
from gemma_agents.runtime import Runtime


@click.command()
@click.option("--model", default="gemma4:12b-mlx", show_default=True)
@click.option("--host", default="http://localhost:11434", show_default=True)
@click.option("--output", type=click.Path(path_type=Path))
def main(model, host, output):
    """Measure actual file outcomes, wall time and tool calls; no model download."""
    console = Console(stderr=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="gemma-eval-") as directory:
        root = Path(directory)
        settings = Settings(
            model=model, ollama_host=host, workspace=root / "workspace",
            storage_dir=root / "storage", database_path=root / "storage/agent.db",
            system_prompt_path=BASE_DIR / "prompts/system.md", max_agent_steps=8)
        session = None
        scenarios = [
            ("create", "Crée result.json contenant exactement {\"ok\": true}. "
             "N'exécute aucune commande.", "result.json", '{"ok": true}'),
            ("edit", "Lis result.json puis remplace true par false dans ce fichier, "
             "sans commande shell.", "result.json", '{"ok": false}'),
            ("resume", "Reprends notre travail : lis result.json et crée resumed.txt "
             "contenant uniquement le mot false si la valeur ok est false. "
             "N'exécute aucune commande.", "resumed.txt", "false"),
        ]
        for name, prompt, filename, expected in scenarios:
            console.print(f"Évaluation {name} · {model}", markup=False)
            events = []
            started = time.monotonic()
            try:
                # Restart the runtime between scenarios; keep the same session.
                with Runtime(settings) as runtime:
                    session = session or runtime.sessions.create()
                    result = runtime.run(session, prompt, emit=events.append)
                    path = settings.workspace / filename
                    actual = path.read_text().strip() if path.exists() else ""
                    if filename.endswith(".json"):
                        passed = json.loads(actual) == json.loads(expected)
                    else:
                        passed = actual == expected
                    passed = passed and result.status == "completed"
                    error = "" if passed else result.content[:500]
            except Exception as exception:
                passed, error = False, str(exception)
            results.append({"scenario": name, "passed": passed,
                            "seconds": round(time.monotonic() - started, 2),
                            "tool_calls": sum(e["type"] == "tool_request"
                                              for e in events), "error": error})
            console.print(results[-1])
    payload = json.dumps({"model": model, "results": results},
                          ensure_ascii=False, indent=2)
    if output:
        output.write_text(payload + "\n")
    click.echo(payload)
    if not all(r["passed"] for r in results):
        raise click.ClickException("Au moins une évaluation a échoué.")


if __name__ == "__main__":
    main()

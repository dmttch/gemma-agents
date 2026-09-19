"""Repeat real-model acceptance scenarios and record reproducible release evidence.

Run only on a trusted macOS Apple Silicon host with an installed local model.
Each case owns a temporary workspace; only its exact validation command receives
a grant. Test files are checked byte-for-byte after execution to detect cheating.
"""

import hashlib
import importlib.metadata
import json
import platform
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import rich_click as click
from evaluate_v4 import interrupted, invalid_tool, multiple_files, repair
from ollama import Client
from rich.console import Console

from gemma_agents.config import BASE_DIR, Settings
from gemma_agents.runtime import Runtime


def python_project(runtime, emit):
    """Fix a package-level CSV aggregation bug without altering acceptance tests."""
    root = runtime.settings.workspace
    (root / "ledger").mkdir()
    (root / "ledger/__init__.py").write_text("")
    (root / "ledger/report.py").write_text(
        "import csv\nfrom decimal import Decimal\nfrom io import StringIO\n\n"
        "def total(text):\n    rows = csv.DictReader(StringIO(text))\n"
        "    return sum(Decimal(r['amount']) for r in rows)\n")
    test = (
        "from decimal import Decimal\nfrom ledger.report import total\n\n"
        "def test_filter_and_decimal():\n"
        "    assert total('amount,status\\n0.10,paid\\n0.20,paid\\n50,pending\\n') "
        "== Decimal('0.30')\n\n"
        "def test_empty():\n"
        "    assert total('amount,status\\n') == Decimal('0')\n"
        "    assert isinstance(total('amount,status\\n'), Decimal)\n")
    (root / "test_report.py").write_text(test)
    (root / "AGENTS.md").write_text(
        "Ne modifie pas test_report.py. Le résultat doit toujours être un Decimal.\n")
    command = shlex.join(["uv", "run", "--no-project", "--offline", "--python",
                          sys.executable, "-m", "pytest", "-q"])
    task = runtime.tasks.create(
        "Corrige ledger.report.total : additionne seulement les lignes status=paid. "
        "Préserve Decimal, y compris si aucune ligne ne correspond. Ne modifie pas "
        "les tests. Ajoute une docstring publique et documente le format CSV dans "
        "README.md. Les tests seront exécutés par le runtime.", [command])
    runtime.permissions.grant("task", task, command)
    result = runtime.run_task(task, emit=emit)
    return (result.verification == "passed"
            and (root / "test_report.py").read_text() == test
            and (root / "README.md").is_file()), result


def long_conversation(runtime, emit):
    """Exercise bounded context after a long history, using current file evidence."""
    session = runtime.sessions.create()
    for number in range(25):
        runtime.sessions.save(session, {"role": "user", "content":
                                        f"Ancien échange {number}. "
                                        + "historique " * 600})
        runtime.sessions.save(session, {"role": "assistant", "content": "Ancien tour."})
    (runtime.settings.workspace / "spec.txt").write_text("RELEASE_CONTEXT_OK")
    result = runtime.run(session,
                         "Lis spec.txt puis crée result.txt avec son contenu exact.",
                         emit=emit)
    return ((runtime.settings.workspace / "result.txt").read_text()
            == "RELEASE_CONTEXT_OK"), result


SCENARIOS = {case.__name__: case for case in
             (repair, multiple_files, interrupted, invalid_tool,
              python_project, long_conversation)}


@click.command()
@click.option("--model", required=True, help="Exact installed local model name.")
@click.option("--host", default="http://localhost:11434", show_default=True)
@click.option("--repetitions", default=3, type=click.IntRange(1, 20), show_default=True)
@click.option("--scenario", multiple=True, type=click.Choice(list(SCENARIOS)))
@click.option("--output", required=True, type=click.Path(path_type=Path))
def main(model, host, repetitions, scenario, output):
    """Run acceptance checks; preserve the report and exit nonzero on failure."""
    console = Console(stderr=True)
    client = Client(host=host, timeout=10)
    try:
        installed = next((m for m in client.list().models if m.model == model), None)
        if installed is None:
            raise click.ClickException("Requested model is not installed.")
        version_response = httpx.get(host.rstrip("/") + "/api/version", timeout=10)
        server = (version_response.json().get("version", "unknown")
                  if version_response.is_success else "not reported by server")
    finally:
        client._client.close()
    source_hash = hashlib.sha256()
    for file in sorted(BASE_DIR.rglob("*")):
        if file.suffix in {".py", ".md"}:
            source_hash.update(str(file.relative_to(BASE_DIR)).encode())
            source_hash.update(file.read_bytes())
    memory = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                            text=True, check=True).stdout.strip()
    report = {"started_at": datetime.now(UTC).isoformat(), "model": model,
              "model_digest": installed.digest, "ollama": str(server),
              "platform": platform.platform(), "machine": platform.machine(),
              "memory_bytes": int(memory), "python": platform.python_version(),
              "package": importlib.metadata.version("gemma-agents"),
              "source_sha256": source_hash.hexdigest(),
              "scenario_sha256": hashlib.sha256(
                  Path(__file__).read_bytes()).hexdigest(),
              "repetitions": repetitions, "results": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    for repeat in range(1, repetitions + 1):
        for name in scenario or SCENARIOS:
            events = []
            start = time.monotonic()
            console.print(f"{repeat}/{repetitions} · {name}")
            with tempfile.TemporaryDirectory(prefix="gemma-acceptance-") as folder:
                root = Path(folder)
                settings = Settings(model=model, ollama_host=host,
                                    workspace=root / "workspace",
                                    storage_dir=root / "storage",
                                    database_path=root / "storage/agent.db",
                                    system_prompt_path=BASE_DIR / "prompts/system.md")
                try:
                    with Runtime(settings) as runtime:
                        passed, result = SCENARIOS[name](runtime, events.append)
                        passed = passed and result.status == "completed"
                        detail = "" if passed else result.content[:2000]
                except Exception as error:
                    passed, detail = False, str(error)
            row = {"scenario": name, "repeat": repeat, "passed": passed,
                   "seconds": round(time.monotonic() - start, 2),
                   "tool_calls": sum(e["type"] == "tool_request" for e in events),
                   "repairs": sum(e["type"] == "repair" for e in events),
                   "human_interventions": 0, "detail": detail,
                   "trace": [e for e in events if e["type"] != "token"]}
            report["results"].append(row)
            report["passed"] = all(r["passed"] for r in report["results"])
            report["complete"] = len(report["results"]) == repetitions * len(
                scenario or SCENARIOS)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            console.print({key: value for key, value in row.items() if key != "trace"})
    if not report["passed"]:
        raise click.ClickException("Acceptance failed; inspect the saved report.")


if __name__ == "__main__":
    main()

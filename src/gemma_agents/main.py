"""Provide command-line entry points for chat, tasks, memory, and the local API."""

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

import httpx
import rich_click as click
from ollama import ResponseError
from rich.console import Console

from gemma_agents import __version__
from gemma_agents.changes import atomic_write
from gemma_agents.config import Settings
from gemma_agents.diagnostics import diagnose
from gemma_agents.locking import WorkspaceBusyError, WorkspaceLease
from gemma_agents.presentation import safe_text
from gemma_agents.runtime import Runtime
from gemma_agents.security.approvals import DenyApprover, TerminalApprover
from gemma_agents.storage import Retention, restore, snapshot
from gemma_agents.terminal import TerminalChat

console = Console()
progress_console = Console(stderr=True)
EXIT_CODES = {"completed": 0, "failed": 1, "blocked": 3, "limited": 4,
              "interrupted": 130}


def json_mode() -> bool:
    """Read the root output option without storing process-global invocation state."""
    context = click.get_current_context(silent=True)
    return bool(context and context.find_root().params.get("as_json"))


class AgentCLI(click.Group):
    """Translate expected operator errors into stable CLI diagnostics and exits."""

    def invoke(self, ctx):
        """Keep JSON errors machine-readable and send human diagnostics to stderr."""
        try:
            return super().invoke(ctx)
        except (click.exceptions.Exit, click.Abort):
            raise
        except KeyboardInterrupt:
            if ctx.params.get("as_json"):
                click.echo(json.dumps({"status": "interrupted"}))
            ctx.exit(130)
        except (ValueError, OSError, sqlite3.Error, httpx.HTTPError,
                ResponseError, RuntimeError) as error:
            code = 3 if isinstance(error, WorkspaceBusyError) else 1
            if ctx.params.get("as_json"):
                click.echo(json.dumps({"status": "error", "error": str(error)}))
                ctx.exit(code)
            diagnostic = click.ClickException(str(error))
            diagnostic.exit_code = code
            raise diagnostic from error


def finish(result):
    """Emit one complete run result and propagate its operational status to the OS."""
    output(asdict(result))
    click.get_current_context().exit(EXIT_CODES.get(result.status, 1))


def approver():
    """Never prompt in JSON mode; persisted exact grants remain available."""
    return DenyApprover() if json_mode() else TerminalApprover()


def output(value):
    """Print structured values as JSON and other values without Rich markup."""
    if json_mode():
        click.echo(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, (list, dict)):
        console.print_json(json.dumps(value, ensure_ascii=False))
    else:
        console.print(safe_text(str(value)), markup=False)


def progress(event):
    """Display step, tool-request, and warning events for noninteractive commands."""
    if event["type"] == "step":
        progress_console.print(f"Étape {event['step']}", style="dim")
    elif event["type"] == "tool_request":
        progress_console.print(f"Outil : {event['name']}", markup=False)
    elif event["type"] == "warning":
        progress_console.print(safe_text(event["content"]), markup=False)


def interact(runtime: Runtime, resume: str | None, plain: bool = False):
    """Start terminal chat in a new or resumed workspace session."""
    TerminalChat(runtime, resume, plain=plain, console=console).run()


@click.group(cls=AgentCLI, invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True,
              help="JSON sur stdout ; aucune approbation interactive.")
@click.option("--model", envvar="AGENT_MODEL", help="Modèle Ollama installé.")
@click.option("--workspace", type=click.Path(path_type=Path),
              envvar="AGENT_WORKSPACE", help="Workspace autorisé.")
@click.option("--resume", help="Reprendre une session en mode interactif.")
@click.option("--plain", is_flag=True, help="Désactiver l'éditeur terminal enrichi.")
@click.version_option(__version__)
@click.pass_context
def main(ctx, workspace, resume, plain, as_json, model):
    """Run a local Ollama agent with chat, tasks, memory, skills, and an API."""
    # Resolve the option before Settings validation and directory creation.
    from dataclasses import replace

    settings = (Settings.from_env(workspace=workspace) if workspace
                else Settings.from_env())
    if model:
        settings = replace(settings, model=model)
    if ctx.invoked_subcommand == "storage":
        ctx.obj = settings
        return
    if as_json and ctx.invoked_subcommand is None:
        raise click.UsageError("--json nécessite une sous-commande, par exemple run.")
    runtime = Runtime(settings)
    ctx.obj = runtime
    ctx.call_on_close(runtime.close)
    if ctx.invoked_subcommand is None:
        interact(runtime, resume, plain)
    elif resume:
        raise click.UsageError("Utilise --resume sans sous-commande ou avec run.")


@main.command()
@click.argument("prompt")
@click.option("--resume")
@click.pass_obj
def run(runtime, prompt, resume):
    """Execute one request and exit."""
    session = runtime.sessions.resume(resume) if resume else runtime.sessions.create()
    result = runtime.run(session, prompt, approver(), progress)
    finish(result)


@main.command()
@click.pass_obj
def sessions(runtime):
    """List sessions in the current workspace."""
    output(runtime.sessions.list())


@main.command()
@click.pass_obj
def doctor(runtime):
    """Inspect configuration, sandbox execution, and installed Ollama models."""
    report = diagnose(runtime)
    output(report)
    click.get_current_context().exit(0 if report["ok"] else 1)


@main.command()
@click.option("--model", "selected", help="Choisir ce modèle sans dialogue.")
@click.pass_obj
def setup(runtime, selected):
    """Select an installed tool-capable model and save the operator configuration."""
    models = [item.model for item in runtime.llm.client.list().models]
    if not selected:
        if json_mode():
            raise ValueError("Utilise setup --model NOM avec --json.")
        if not models:
            raise ValueError("Installe un modèle Ollama compatible avec les outils.")
        output({"installed_models": models})
        selected = click.prompt("Modèle", type=click.Choice(models))
    runtime.llm.validate_model(selected)
    target = runtime.settings.storage_dir / "config.toml"
    atomic_write(target, "model = " + json.dumps(selected) + "\n")
    output({"model": selected, "configuration": str(target)})


@main.group()
def tasks():
    """Manage persistent tasks."""


@tasks.command("add")
@click.argument("prompt")
@click.option("--check", "checks", multiple=True,
              help="Commande de validation à exécuter après la tâche (répétable).")
@click.option("--allow-command", multiple=True,
              help="Autoriser exactement cette commande pour cette tâche.")
@click.option("--grant-hours", default=8, type=click.IntRange(1, 168))
@click.pass_obj
def task_add(runtime, prompt, checks, allow_command, grant_hours):
    """Create a task with verification commands and optional expiring command grants."""
    for command in allow_command:
        runtime.permissions.validate(command)
    task_id = runtime.tasks.create(prompt, list(checks))
    for command in allow_command:
        runtime.permissions.grant("task", task_id, command, grant_hours)
    output(task_id)


@tasks.command("list")
@click.pass_obj
def task_list(runtime):
    """Display persistent tasks in the current workspace."""
    output(runtime.tasks.list())


@tasks.command("run")
@click.argument("task_id")
@click.pass_obj
def task_run(runtime, task_id):
    """Execute a queued task with terminal approval prompts."""
    finish(runtime.run_task(task_id, approver(), progress))


@tasks.command("retry")
@click.argument("task_id")
@click.pass_obj
def task_retry(runtime, task_id):
    """Requeue a task in the same session after inspecting its previous effects."""
    runtime.tasks.recover(task_id)
    output("Tâche remise en attente.")


@main.group()
def schedule():
    """Schedule recurring tasks using five-field cron expressions."""


@schedule.command("add")
@click.argument("expression")
@click.argument("prompt")
@click.option("--timezone", default="Europe/Paris", show_default=True)
@click.option("--allow-command", multiple=True,
              help="Autoriser cette commande pour les occurrences avant expiration.")
@click.option("--grant-hours", default=8, type=click.IntRange(1, 168))
@click.pass_obj
def schedule_add(runtime, expression, prompt, timezone, allow_command, grant_hours):
    """Create a recurring task schedule with optional command grants."""
    for command in allow_command:
        runtime.permissions.validate(command)
    schedule_id = runtime.tasks.schedule(prompt, expression, timezone)
    for command in allow_command:
        runtime.permissions.grant("schedule", schedule_id, command, grant_hours)
    output(schedule_id)


@schedule.command("list")
@click.pass_obj
def schedule_list(runtime):
    """Display recurring schedules in the current workspace."""
    output(runtime.tasks.schedules())


@schedule.command("pause")
@click.argument("schedule_id")
@click.option("--resume", is_flag=True)
@click.pass_obj
def schedule_pause(runtime, schedule_id, resume):
    """Pause a schedule or resume it when the resume flag is set."""
    runtime.tasks.pause(schedule_id, paused=not resume)
    output("Planification mise à jour.")


@main.command()
@click.option("--once", is_flag=True, help="Traiter la file une fois puis quitter.")
@click.pass_obj
def worker(runtime, once):
    """Process queued and due tasks, denying sensitive actions without existing grants.
    """
    try:
        while True:
            output({"processed": runtime.tick()})
            if once:
                break
            time.sleep(5)
    except KeyboardInterrupt:
        output("Worker arrêté.")


@main.group()
def memory():
    """Manage semantic memories for the current workspace."""


@memory.command("add")
@click.argument("content")
@click.pass_obj
def memory_add(runtime, content):
    """Save a durable fact to the workspace's semantic memory."""
    output(runtime.memory.remember(content))


@memory.command("search")
@click.argument("query")
@click.pass_obj
def memory_search(runtime, query):
    """Display workspace memories ranked by semantic relevance."""
    output(runtime.memory.search(query))


@memory.command("forget")
@click.argument("memory_id")
@click.pass_obj
def memory_forget(runtime, memory_id):
    """Delete one memory owned by the current workspace."""
    output(runtime.memory.forget(memory_id))


@main.command()
@click.pass_obj
def skills(runtime):
    """List installed trusted skills."""
    output(runtime.skills.list_skills())


@main.command()
@click.option("--port", default=8765, type=click.IntRange(1024, 65535))
@click.option("--scheduler", is_flag=True, help="Activer le worker de tâches.")
@click.pass_obj
def serve(runtime, port, scheduler):
    """Start the authenticated HTTP/WebSocket API on the loopback interface."""
    import uvicorn

    from gemma_agents.api import create_app

    try:
        app = create_app(runtime, scheduler=scheduler)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    uvicorn.run(app, host="127.0.0.1", port=port, workers=1)


@main.group()
def storage():
    """Back up or restore private SQLite storage (not workspace files or skills)."""


@storage.command("backup")
@click.argument("destination", type=click.Path(path_type=Path))
@click.pass_obj
def storage_backup(settings, destination):
    """Save a consistent snapshot to a new path; existing files are never replaced."""
    lease = WorkspaceLease(settings.storage_dir, shared=True)
    try:
        if destination.expanduser().resolve().is_relative_to(settings.workspace):
            raise ValueError("Conserve les sauvegardes hors du workspace de l'agent.")
        output({"backup": str(snapshot(settings.database_path, destination))})
    finally:
        lease.close()


@storage.command("restore")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--yes", is_flag=True, help="Confirmer le remplacement de toute la base.")
@click.pass_obj
def storage_restore(settings, source, yes):
    """Replace all stored workspaces from a backup, preserving a rescue snapshot."""
    confirm_change(yes, "Remplacer la base et révoquer les permissions restaurées ?")
    rescue = restore(source, settings.database_path)
    output({"restored": str(settings.database_path),
            "rescue_backup": str(rescue) if rescue else None})


def confirm_change(yes: bool, message: str) -> None:
    """Require explicit confirmation; JSON callers must supply --yes."""
    if not yes:
        if json_mode():
            raise ValueError("Cette opération nécessite --yes en mode JSON.")
        click.confirm(message, abort=True)


@main.group()
def session():
    """Export or delete a conversation in the current workspace."""


@session.command("delete")
@click.argument("session_id")
@click.option("--yes", is_flag=True, help="Confirmer la suppression de la session.")
@click.pass_obj
def session_delete(runtime, session_id, yes):
    """Delete history, edits, checks and associated tasks; preserve workspace files."""
    confirm_change(yes, f"Supprimer la session {session_id} et ses tâches associées ?")
    Retention(runtime.database, runtime.settings.workspace).delete_session(session_id)
    output({"deleted": session_id})


@session.command("export")
@click.argument("session_id")
@click.argument("destination", type=click.Path(path_type=Path))
@click.pass_obj
def session_export(runtime, session_id, destination):
    """Export Markdown to a new operator-selected file, without overwriting."""
    content = runtime.sessions.export(session_id)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(content)
    output({"exported": session_id, "path": str(destination.absolute())})


@main.command("prune-audit")
@click.option("--days", default=90, type=click.IntRange(1), show_default=True)
@click.option("--apply", "apply_changes", is_flag=True,
              help="Supprimer les lignes ; sans cette option, compter seulement.")
@click.pass_obj
def prune_audit(runtime, days, apply_changes):
    """Count or delete old workspace audit entries; retain conversations and edits."""
    count = Retention(runtime.database, runtime.settings.workspace).prune_audit(
        days, apply=apply_changes)
    output({"records": count, "applied": apply_changes, "retention_days": days})


if __name__ == "__main__":
    main()

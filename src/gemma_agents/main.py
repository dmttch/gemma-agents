"""Provide command-line entry points for chat, tasks, memory, and the local API."""

import json
import time
from pathlib import Path

import rich_click as click
from rich.console import Console

from gemma_agents.config import Settings
from gemma_agents.runtime import Runtime
from gemma_agents.security.approvals import TerminalApprover
from gemma_agents.terminal import TerminalChat

console = Console()


def output(value):
    """Print structured values as JSON and other values without Rich markup."""
    if isinstance(value, (list, dict)):
        console.print_json(json.dumps(value, ensure_ascii=False))
    else:
        console.print(str(value), markup=False)


def progress(event):
    """Display step, tool-request, and warning events for noninteractive commands."""
    if event["type"] == "step":
        console.print(f"Étape {event['step']}", style="dim")
    elif event["type"] == "tool_request":
        output(f"Outil : {event['name']}")
    elif event["type"] == "warning":
        output(event["content"])


def interact(runtime: Runtime, resume: str | None, plain: bool = False):
    """Start terminal chat in a new or resumed workspace session."""
    TerminalChat(runtime, resume, plain=plain, console=console).run()


@click.group(invoke_without_command=True)
@click.option("--workspace", type=click.Path(path_type=Path),
              envvar="AGENT_WORKSPACE", help="Workspace autorisé.")
@click.option("--resume", help="Reprendre une session en mode interactif.")
@click.option("--plain", is_flag=True, help="Désactiver l'éditeur terminal enrichi.")
@click.version_option("0.4.0")
@click.pass_context
def main(ctx, workspace, resume, plain):
    """Run a local Ollama agent with chat, tasks, memory, skills, and an API."""
    # Resolve the option before Settings validation and directory creation.
    if workspace:
        import os
        os.environ["AGENT_WORKSPACE"] = str(workspace)
    try:
        runtime = Runtime(Settings.from_env())
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
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
    result = runtime.run(session, prompt, TerminalApprover(), progress)
    output({"session_id": session, "status": result.status, "content": result.content})


@main.command()
@click.pass_obj
def sessions(runtime):
    """List sessions in the current workspace."""
    output(runtime.sessions.list())


@main.command()
@click.pass_obj
def doctor(runtime):
    """Inspect configuration, sandbox execution, and installed Ollama models."""
    output({"workspace": str(runtime.settings.workspace),
            "storage": str(runtime.settings.storage_dir),
            "sandbox": runtime.settings.sandbox,
            "sandbox_available": runtime.runner.available,
            "web_search": bool(runtime.settings.searxng_url)})
    try:
        output(runtime.runner.run(["pwd"], timeout=10))
    except Exception as error:
        output(f"Sandbox : {error}")
    try:
        models = [model.model for model in runtime.llm.client.list().models]
        output({"installed_models": models,
                "requested_model": runtime.settings.model,
                "embedding_model": runtime.settings.embedding_model})
    except Exception as error:
        output(f"Ollama indisponible : {error}")


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
    output(runtime.run_task(task_id, TerminalApprover(), progress).content)


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


if __name__ == "__main__":
    main()

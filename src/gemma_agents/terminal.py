"""Scrollback-friendly terminal chat: prompt-toolkit input and Rich output."""

import asyncio
import json
import shlex
import sys
import time

import rich_click as click
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from gemma_agents import __version__
from gemma_agents.checkpoints import Checkpoints
from gemma_agents.presentation import safe_text, tool_content
from gemma_agents.tasks import Planner
from gemma_agents.terminal_work import WorkView

COMMANDS = {
    "/rename": "Renommer la session : /rename titre",
    "/preview": "Lire les derniers échanges d'une session : /preview identifiant",
    "/fork": "Dupliquer la conversation (workspace partagé)",
    "/export": "Exporter en Markdown : /export chemin-dans-workspace.md",
    "/model": "Afficher les modèles ou choisir : /model nom",
    "/permissions": "Voir les permissions ; revoke identifiant pour révoquer",
    "/allow": "Autoriser une commande exacte 8 h : /allow uv run pytest -q",
    "/review": "Revoir les éditions ; on/off pour préparer avant application",
    "/help": "Afficher les commandes et raccourcis",
    "/new": "Créer une session dans ce workspace",
    "/sessions": "Lister les sessions",
    "/resume": "Reprendre une session : /resume identifiant",
    "/session": "Afficher la session courante",
    "/history": "Afficher les derniers échanges",
    "/plan": "Consulter le plan et sa progression",
    "/checkpoint": "Consulter le point de reprise durable",
    "/diff": "Voir les éditions enregistrées dans cette session",
    "/undo": "Annuler une édition : /undo [identifiant]",
    "/check": "Lancer une vérification : /check uv run pytest -q",
    "/checks": "Consulter les vérifications observées",
    "/tasks": "Lister les tâches persistantes",
    "/task": "Exécuter une tâche : /task identifiant",
    "/retry": "Remettre une tâche en attente après inspection",
    "/tools": "Activer ou masquer le détail des résultats d'outils",
    "/last": "Afficher les résultats d'outils du dernier tour",
    "/clear": "Effacer l'écran, conserver la conversation",
    "/quit": "Quitter (la session reste enregistrée)",
}


class CommandCompleter(Completer):
    """Complete slash commands, session identifiers, and explicit workspace references.
    """

    def __init__(self, runtime):
        """Retain runtime access for session and workspace-file completion."""
        self.runtime = runtime

    def get_completions(self, document, complete_event):
        """Yield replacements for the command or reference immediately before the
        cursor.
        """
        text = document.text_before_cursor
        word = text.rsplit(" ", 1)[-1]
        if word.startswith("@"):
            prefix = word[1:]
            for path in self.runtime.project.files():
                if path.startswith(prefix):
                    ref = '@"' + path + '"' if " " in path else "@" + path
                    yield Completion(ref, start_position=-len(word))
        elif text.startswith("/resume "):
            prefix = text.removeprefix("/resume ")
            for session in self.runtime.sessions.list():
                if session["id"].startswith(prefix):
                    yield Completion(session["id"], start_position=-len(prefix),
                                     display_meta=session["title"] or
                                     session["created_at"][:16])
        elif text.startswith("/") and " " not in text:
            for command, description in COMMANDS.items():
                if command.startswith(text):
                    yield Completion(command, start_position=-len(text),
                                     display_meta=description)


def input_bindings() -> KeyBindings:
    """Bind Enter to submit and Alt+Enter or Ctrl+J to insert a newline."""
    bindings = KeyBindings()

    @bindings.add("enter")
    def submit(event):
        """Dismiss active completion and submit the current input buffer."""
        if event.current_buffer.complete_state:
            event.current_buffer.complete_state = None
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    @bindings.add("c-j")
    def newline(event):
        """Insert a literal newline without submitting the prompt."""
        event.current_buffer.insert_text("\n")

    return bindings


class TerminalRenderer:
    """Render agent progress, tool output, and final answers into terminal scrollback.
    """

    def __init__(self, console: Console, interactive: bool):
        """Configure the output console and initialize per-turn display state."""
        self.console = console
        self.interactive = interactive
        self.verbose = False
        self.live = None
        self.stream = ""
        self.tools = []
        self.started = time.monotonic()
        self.context = ""
        self.step = 0

    def begin(self):
        """Reset timing, tool history, and streamed text for a new turn."""
        self.tools = []
        self.started = time.monotonic()
        self.stream = ""
        self.step = 0

    def pause(self):
        """Stop transient live output before printing or requesting terminal input."""
        if self.live:
            self.live.stop()
            self.live = None

    def working(self):
        """Start or refresh the interactive progress spinner and streamed Markdown."""
        if not self.interactive:
            return
        body = Spinner("dots11", text=Text(
            f" Gemma travaille · étape {self.step} · Ctrl+C pour interrompre",
            style="dim"))
        if self.stream:
            body = Group(Markdown(safe_text(self.stream)), body)
        if not self.live:
            self.live = Live(body, console=self.console, transient=True,
                             refresh_per_second=8, vertical_overflow="ellipsis")
            self.live.start()
        else:
            self.live.update(body)

    def flush_stream(self):
        """Commit buffered streamed Markdown to scrollback and clear the buffer."""
        self.pause()
        if self.stream:
            self.console.print(Markdown(safe_text(self.stream)))
            self.stream = ""

    def __call__(self, event):
        """Dispatch a runtime event to its progress, text, warning, or tool
        presentation.
        """
        kind = event["type"]
        if kind == "step":
            self.flush_stream()
            self.step = event["step"]
            self.working()
        elif kind == "context":
            self.context = f"contexte {event['chars'] * 100 // event['budget']} %"
            if event["omitted"] or event["compacted"]:
                self.pause()
                self.console.print("  ↳ contexte condensé · historique conservé",
                                   style="dim")
                self.working()
        elif kind == "token":
            self.stream += event["content"]
            self.working()
        elif kind == "tool_request":
            self.flush_stream()
            arguments = event["arguments"]
            detail = arguments.get("command", arguments.get("path", ""))
            if not detail and arguments:
                detail = json.dumps(arguments, ensure_ascii=False)
            self.console.print(Text(f"  › {event['name']}  "
                                    + safe_text(str(detail))[:140], style="cyan"))
            self.working()
        elif kind == "tool_result":
            self.pause()
            content = tool_content(event["name"], event["content"])
            self.tools.append((event["name"], content))
            error = content.startswith(("ERREUR", "ACTION REFUSÉE", "TIMEOUT"))
            if content.startswith("EXIT CODE:"):
                error = not content.startswith("EXIT CODE: 0\n")
            if content.startswith("VÉRIFICATION :"):
                error = not content.startswith("VÉRIFICATION : passed\n")
            if self.verbose or error:
                self.console.print(Panel(Text("\n".join(content.splitlines()[:12])),
                                         border_style="red" if error else "dim"))
            else:
                self.console.print(Text("    ↳ " + content.split("\n")[0][:120],
                                         style="dim"))
            self.working()
        elif kind == "warning":
            self.pause()
            self.console.print(Text(event["content"], style="yellow"))
        elif kind == "final":
            self.pause()

    def finish(self, result):
        """Display the final answer with elapsed time, tool count, and context usage."""
        self.pause()
        self.stream = ""
        self.console.print()
        self.console.print(Panel(Markdown(safe_text(result.content)), title="Gemma",
                                 title_align="left", border_style="cyan",
                                 padding=(1, 2)))
        elapsed = time.monotonic() - self.started
        self.console.print(Text(
            f"  {result.status} · {elapsed:.1f}s · {len(self.tools)} outils"
            f" · {self.context}", style="dim"))
        self.console.print()


class TerminalApproval:
    """Integrate one-call terminal approval prompts with the progress renderer."""

    def __init__(self, renderer: TerminalRenderer):
        """Bind approval prompts to the active renderer and its console."""
        self.renderer = renderer

    def confirm(self, tool_name: str, arguments: dict, reason: str) -> bool:
        """Pause progress, show the call, and prompt for approval or propagate
        interruption.
        """
        self.renderer.pause()
        self.renderer.console.print(Panel(Group(
            Text(safe_text(reason)),
            Syntax(safe_text(json.dumps(arguments, ensure_ascii=False, indent=2)),
                   "json", word_wrap=True, background_color="default")),
            title=Text(f"Autorisation · {tool_name}"), title_align="left",
            border_style="yellow"))
        try:
            approved = click.confirm("Autoriser cet appel uniquement ?", default=False)
            if approved:
                self.renderer.working()
            return approved
        except click.Abort:
            raise KeyboardInterrupt from None
        except EOFError:
            return False


class TerminalChat:
    """Coordinate terminal input, local slash commands, and interactive agent execution.
    """

    def __init__(self, runtime, resume=None, *, plain=False, console=None):
        """Select a session and configure rich or plain input based on terminal support.
        """
        self.runtime = runtime
        self.console = console or Console()
        self.session = (runtime.sessions.resume(resume) if resume
                        else runtime.sessions.create())
        self.interactive = not plain and sys.stdin.isatty() and self.console.is_terminal
        self.renderer = TerminalRenderer(self.console, self.interactive)
        self.approver = TerminalApproval(self.renderer)
        self.editor = None
        self.make_editor()

    def make_editor(self):
        """Build a multiline prompt editor with session history when interactive."""
        if self.interactive:
            self.editor = PromptSession(
                multiline=True, key_bindings=input_bindings(),
                history=self.history(), auto_suggest=AutoSuggestFromHistory(),
                completer=CommandCompleter(self.runtime), complete_while_typing=True,
                prompt_continuation="  · ", reserve_space_for_menu=5,
                bottom_toolbar=self.toolbar,
                style=Style.from_dict({"prompt": "bold ansicyan",
                                       "bottom-toolbar": "bg:ansiblack ansiwhite"}))

    def history(self):
        """Return user messages from the current session as in-memory input history."""
        return InMemoryHistory(history_strings=[
            m["content"] for m in self.runtime.sessions.history(self.session)
            if m["role"] == "user"])

    def toolbar(self):
        """Build the prompt toolbar with session identity and context usage."""
        return [("", f" {self.session[:8]} · {self.renderer.context or 'prêt'}"
                 " | ↵ envoyer · Alt+↵ ligne · Ctrl+R · /help ")]

    def select_session(self, session):
        """Validate and switch sessions, refreshing editor history and display state."""
        self.session = self.runtime.sessions.resume(session)
        self.make_editor()
        self.renderer.context = ""
        self.renderer.tools = []

    def banner(self):
        """Display model/workspace details and any unfinished-run recovery notice."""
        settings = self.runtime.settings
        model = self.runtime.sessions.model(self.session) or settings.model
        self.console.print(Panel(Group(
            Text(f"GEMMA AGENTS  /  {__version__}", style="bold cyan"),
            Text(f"{model}  ·  {settings.workspace}", style="dim"),
            Text("Décris une tâche, colle du code ou tape / pour les commandes."),
        ), border_style="cyan", padding=(1, 2)))
        self.console.print(Text(f"Session {self.session} · sandbox {settings.sandbox}",
                                style="dim"))
        if self.interactive:
            self.console.print("Entrée : envoyer · Alt+Entrée : nouvelle ligne · "
                               "Ctrl+C : interrompre", style="dim")
        if settings.sandbox == "off":
            self.console.print("Sandbox désactivé : processus non isolés.",
                               style="yellow")
        with self.runtime.database.connect() as db:
            row = db.execute("SELECT status FROM runs WHERE session_id=? "
                             "ORDER BY started_at DESC LIMIT 1",
                             (self.session,)).fetchone()
        if row and row[0] in {"running", "interrupted", "failed"}:
            self.console.print("Exécution précédente inachevée : inspecte /history, "
                               "/diff et /checkpoint avant de continuer.",
                               style="yellow")

    def table(self, title, rows, columns):
        """Render selected row fields as sanitized text in a Rich table."""
        table = Table(title=title, title_justify="left", expand=True,
                      border_style="dim", show_lines=False)
        for label, _ in columns:
            table.add_column(label, overflow="fold")
        for row in rows:
            table.add_row(*(Text(safe_text(str(row.get(key, ""))))
                            for _, key in columns))
        self.console.print(table if rows else Text("Aucun élément.", style="dim"))

    def command(self, prompt):
        """Execute a local slash command, returning False only when chat should exit."""
        command, _, argument = prompt.partition(" ")
        argument = argument.strip()
        if command in {"/quit", "/exit"}:
            return False
        if command == "/help":
            self.table("Commandes", [{"name": k, "help": v}
                                     for k, v in COMMANDS.items()],
                       [("Commande", "name"), ("Description", "help")])
        elif command == "/session":
            self.console.print(self.session)
        elif command in {"/new", "/resume"}:
            self.select_session(self.runtime.sessions.create() if command == "/new"
                                else argument)
            self.banner()
        elif command == "/sessions":
            self.table("Sessions", self.runtime.sessions.list(argument),
                       [("Identifiant", "id"), ("Titre", "title"),
                        ("Créée le", "created_at")])
        elif command == "/rename":
            self.runtime.sessions.rename(self.session, argument)
            self.console.print("Session renommée.")
        elif command == "/preview":
            for message in self.runtime.sessions.history(argument)[-6:]:
                self.console.print(Panel(Text(safe_text(message.get("content", "")
                                                       [:3000])),
                                         title=message["role"]))
        elif command == "/fork":
            self.select_session(self.runtime.sessions.fork(self.session))
            self.console.print("Conversation dupliquée ; "
                               "les fichiers restent partagés.")
            self.banner()
        elif command == "/export":
            target = self.runtime.project.path(argument)
            if not argument or target.suffix != ".md":
                raise ValueError("Choisis un fichier .md dans le workspace.")
            with target.open("x", encoding="utf-8") as stream:
                stream.write(self.runtime.sessions.export(self.session))
            self.console.print(Text(f"Conversation exportée : {target}"))
        elif command == "/model":
            if argument:
                self.runtime.select_model(self.session, argument)
            current = (self.runtime.sessions.model(self.session)
                       or self.runtime.settings.model)
            self.console.print(Text(f"Prochain tour : {current}"))
            if not argument:
                for model in self.runtime.llm.client.list().models:
                    self.console.print(Text(model.model))
        elif command == "/permissions":
            if argument.startswith("revoke "):
                self.runtime.permissions.revoke(int(argument.split()[1]))
            grants = self.runtime.permissions.list()
            for grant in grants:
                grant["command"] = shlex.join(json.loads(grant["command"]))
            self.table("Permissions exactes du workspace", grants,
                       [("ID", "id"), ("Portée", "scope"), ("Cible", "scope_id"),
                        ("Commande", "command"), ("Expiration", "expires_at")])
        elif command == "/allow":
            grant = self.runtime.permissions.grant("session", self.session, argument)
            self.console.print(Text(f"Permission {grant} accordée pour 8 h : "
                                    + argument))
        elif command == "/review":
            if argument in {"on", "off"}:
                if argument == "on":
                    # A pre-existing process could still mutate files after review
                    # mode starts, bypassing the intended proposal-only workflow.
                    if any(p.process.poll() is None
                           for p in self.runtime.runner.processes):
                        raise ValueError("Arrête les processus actifs avant ce mode.")
                    self.runtime.review_sessions.add(self.session)
                else:
                    self.runtime.review_sessions.discard(self.session)
                self.console.print("Préparation avant application : " + argument)
            else:
                store = self.runtime.changes(self.session)
                if argument:
                    parts = argument.split()
                    selected = [int(p) for p in parts[2:]] or None
                    result = store.review(int(parts[1]), parts[0], selected)
                elif self.interactive:
                    from gemma_agents.terminal_review import review_changes
                    result = review_changes(store, self.console)
                else:
                    raise ValueError("Usage : /review accept|reject|undo ID [blocs]")
                if result:
                    self.runtime.sessions.save(self.session, {"role": "user",
                                                              "content": prompt})
                    self.runtime.sessions.save(self.session, {"role": "assistant",
                                                              "content": result})
                    self.console.print(Text(result))
        elif command == "/history":
            for message in self.runtime.sessions.history(self.session)[-30:]:
                content = message.get("content", "")
                if content:
                    self.console.print(Panel(Text(safe_text(content[:8000])),
                                             title=message["role"], border_style="dim"))
        elif command == "/plan":
            plan = json.loads(Planner(self.runtime.database, self.session).get_plan())
            self.table("Plan", plan, [("État", "status"), ("Étape", "step")])
        elif command == "/checkpoint":
            self.console.print_json(Checkpoints(self.runtime.database,
                                                self.session).get_checkpoint())
        elif command == "/diff":
            self.console.print(Syntax(safe_text(self.runtime.changes(self.session).diff()),
                                      "diff", word_wrap=True,
                                      background_color="default"))
        elif command == "/undo":
            result = self.runtime.changes(self.session).undo(
                int(argument) if argument else None)
            self.runtime.sessions.save(self.session, {"role": "user",
                                                      "content": prompt})
            self.runtime.sessions.save(self.session, {"role": "assistant",
                                                      "content": result})
            self.console.print(Text(result))
        elif command == "/check":
            if not argument:
                raise ValueError("Usage : /check uv run pytest -q")
            try:
                result = self.runtime.check(self.session, argument, self.approver)
            finally:
                self.renderer.pause()
            self.runtime.sessions.save(self.session, {"role": "user",
                                                      "content": prompt})
            self.runtime.sessions.save(self.session, {"role": "assistant",
                                                      "content": result})
            self.console.print(Text(safe_text(result)))
        elif command == "/checks":
            self.table("Vérifications observées (à leur date d'exécution)",
                       self.runtime.verifications(self.session).list(),
                       [("Commande", "command"), ("Résultat", "status"),
                        ("Validité", "freshness"), ("Date", "created_at")])
        elif command == "/tasks":
            self.table("Tâches", self.runtime.tasks.list(),
                       [("Identifiant", "id"), ("État", "status"),
                        ("Demande", "prompt")])
        elif command == "/task":
            self.renderer.begin()
            result = self.execute("", task_id=argument)
            self.renderer.finish(result)
            self.select_session(result.session_id)
            self.console.print(Text(f"Session de la tâche : {self.session}",
                                    style="dim"))
        elif command == "/retry":
            if self.runtime.lock.locked():
                raise ValueError("Une tâche est déjà en cours.")
            self.runtime.tasks.recover(argument)
            self.console.print("Tâche en attente ; /task identifiant pour reprendre.")
        elif command == "/tools":
            self.renderer.verbose = not self.renderer.verbose
            self.console.print("Détails des outils : "
                               + ("activés" if self.renderer.verbose else "masqués"))
        elif command == "/last":
            for name, content in self.renderer.tools:
                self.console.print(Panel(Text(content), title=name, border_style="dim"))
        elif command == "/clear":
            self.console.clear()
            self.banner()
        else:
            self.console.print(Text(f"Commande inconnue : {command}. Tape /help.",
                                    style="yellow"))
        return True

    def execute(self, prompt, task_id=None):
        """Run chat or a task in the full-screen work view or plain event renderer."""
        if self.interactive:
            view = WorkView(self.runtime, self.session, prompt, task_id=task_id)
            result = asyncio.run(view.run())
            # Replay durable events into scrollback after leaving the full-screen
            # view, without starting another transient spinner.
            self.renderer.interactive = False
            try:
                for event in view.events:
                    if event["type"] != "step":
                        self.renderer(event)
            finally:
                self.renderer.interactive = True
            return result
        if task_id:
            return self.runtime.run_task(task_id, self.approver, self.renderer)
        return self.runtime.run(self.session, prompt, self.approver, self.renderer)

    def run(self):
        """Read prompts until exit, handling commands and preserving interrupted
        sessions.
        """
        self.banner()
        try:
            while True:
                try:
                    prompt = (self.editor.prompt([("class:prompt", "❯ ")])
                              if self.editor else click.prompt("Vous",
                                                               prompt_suffix=" ❯ "))
                except KeyboardInterrupt:
                    self.console.print("Saisie annulée · /quit pour quitter.",
                                       style="dim")
                    continue
                except (EOFError, click.Abort):
                    break
                prompt = prompt.strip()
                if not prompt:
                    continue
                try:
                    if prompt.startswith("/"):
                        if not self.command(prompt):
                            break
                        continue
                    self.renderer.begin()
                    result = self.execute(prompt)
                    self.renderer.finish(result)
                except KeyboardInterrupt:
                    self.renderer.pause()
                    self.renderer.stream = ""
                    self.console.print("Tour interrompu · session conservée. "
                                       "Tu peux inspecter /diff puis continuer.",
                                       style="yellow")
                except Exception as error:
                    self.renderer.pause()
                    self.console.print(Text(f"Erreur : {safe_text(str(error))}",
                                            style="red"))
        finally:
            self.renderer.pause()
        self.console.print(Text(f"À bientôt · reprise : gemma-agents --resume "
                                f"{self.session}", style="dim"))

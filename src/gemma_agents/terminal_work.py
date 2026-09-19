"""Interactive running view. Only the UI thread touches prompt-toolkit widgets."""

import asyncio
import json
import threading

from prompt_toolkit.application import Application
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

from gemma_agents.control import RunControl
from gemma_agents.presentation import tool_content


class WorkApprover:
    """Bridge a worker's blocking approval request to the live UI with cancellation."""

    def __init__(self, request, control):
        """Retain the UI request callback and cooperative run control."""
        self.request, self.control = request, control

    def confirm(self, tool_name, arguments, reason):
        """Publish a decision request and wait while periodically checking cancellation.
        """
        decision = {"tool": tool_name, "arguments": arguments, "reason": reason,
                    "event": threading.Event(), "approved": False}
        self.request(decision)
        while not decision["event"].wait(0.1):
            self.control.check_cancelled()
        return decision["approved"]


class WorkView:
    """Keep a full-screen log and steering input responsive during background work."""

    def __init__(self, runtime, session_id, prompt, *, task_id=None):
        """Configure widgets, key bindings, and shared state for a chat or task run."""
        self.runtime, self.session_id, self.prompt = runtime, session_id, prompt
        self.task_id = task_id
        self.control = RunControl()
        self.pending = None
        self.result = None
        self.error = None
        self.events = []
        self.loop = None
        self.log = TextArea(read_only=True, scrollbar=True, wrap_lines=True,
                            focusable=True)
        self.input = TextArea(height=3, multiline=True, prompt="❯ ", wrap_lines=True)
        self.status = TextArea(height=1, read_only=True, text=
                               "Entrée : précision · /pause · /continue · /stop · Tab")
        bindings = KeyBindings()

        @bindings.add("enter")
        def submit(event):
            """Send input only from the input widget and display command errors in the
            log.
            """
            if event.app.layout.current_control != self.input.control:
                return
            message = self.input.text.strip()
            self.input.text = ""
            try:
                self.submit(message)
            except Exception as error:
                self.append(f"\n⚠ {error}\n")

        @bindings.add("escape", "enter")
        @bindings.add("c-j")
        def newline(event):
            """Insert a newline only when the steering input has focus."""
            if event.app.layout.current_control == self.input.control:
                self.input.buffer.insert_text("\n")

        @bindings.add("c-c")
        def cancel(event):
            """Request cooperative cancellation through the same path as the stop
            command.
            """
            self.submit("/stop")

        @bindings.add("tab")
        def focus(event):
            """Move keyboard focus between the log and steering input."""
            event.app.layout.focus_next()

        self.app = Application(
            layout=Layout(HSplit([Frame(self.log, title="Gemma · travail en cours"),
                                  self.status, Frame(self.input, title="Intervenir")]),
                          focused_element=self.input),
            key_bindings=bindings, full_screen=True, mouse_support=True,
            style=Style.from_dict({"frame.label": "bold ansicyan"}))

    def append(self, content):
        """Append sanitized bounded log text while preserving a reader's cursor
        position.
        """
        clean = "".join(c for c in str(content) if c in "\n\t" or (
            ord(c) >= 32 and not 127 <= ord(c) <= 159))
        text = (self.log.text + clean)[-60000:]
        # Follow new output while typing, but avoid pulling a reader away from
        # an older log position when the log itself has focus.
        position = (len(text) if self.app.layout.current_control == self.input.control
                    else min(self.log.buffer.cursor_position, len(text)))
        self.log.buffer.set_document(Document(text, position), bypass_readonly=True)
        self.app.invalidate()

    def event(self, event):
        """Render a runtime event and retain non-token events for later scrollback
        replay.
        """
        kind = event["type"]
        if kind != "token":
            self.events.append(event)
        if kind == "token":
            self.append(event["content"])
        elif kind == "tool_request":
            self.append(f"\n› {event['name']} " + json.dumps(
                event["arguments"], ensure_ascii=False)[:300] + "\n")
        elif kind == "tool_result":
            content = tool_content(event["name"], event["content"])
            self.append("↳ " + content[:1200] + "\n")
        elif kind in {"warning", "steering"}:
            self.append(f"\n{kind} : {event['content']}\n")
        elif kind == "step":
            self.append(f"\n── Étape {event['step']} ──\n")
        elif kind == "repair":
            self.append(f"\nCorrection automatique {event['attempt']} · "
                        f"{event['remaining_steps']} étapes restantes\n")

    def request(self, decision):
        """Display a pending approval and the supported one-call or session decisions.
        """
        self.pending = decision
        self.append(f"\nAUTORISATION · {decision['tool']}\n{decision['reason']}\n"
                    + json.dumps(decision["arguments"], ensure_ascii=False, indent=2)
                    + "\n/once : cet appel · /allow : commande pour cette session (8 h)"
                    " · /deny : refuser\n")

    def submit(self, message):
        """Handle live control/approval commands or queue expanded user steering."""
        if not message:
            return
        if message == "/pause":
            self.control.pause()
            self.append("\nPause demandée ; l'action en cours peut finir.\n")
        elif message == "/continue":
            self.control.pause(False)
            self.append("\nExécution reprise.\n")
        elif message == "/stop":
            self.control.cancel()
            self.append("\nArrêt demandé ; attente de la fin de l'action en cours.\n")
        elif message in {"/once", "/allow", "/deny"}:
            if not self.pending:
                raise ValueError("Aucune approbation en attente.")
            pending = self.pending
            if message == "/allow":
                if pending["tool"] not in {"run_command", "run_check"}:
                    raise ValueError("Pour cet outil, utilise /once ou /deny.")
                session = self.session_id
                if self.task_id:
                    session = self.runtime.tasks.get(self.task_id)["session_id"]
                self.runtime.permissions.grant("session", session,
                                               pending["arguments"]["command"])
            pending["approved"] = message != "/deny"
            pending["event"].set()
            self.pending = None
            self.append(f"\nDécision : {message}\n")
        elif message.startswith("/"):
            raise ValueError("Pendant le travail : /pause, /continue, /stop, "
                             "/once, /allow, /deny.")
        else:
            self.control.steer(self.runtime.project.expand_references(message))
            self.append("\nPrécision en attente ; intégration entre deux actions.\n")

    async def run(self):
        """Run the UI alongside a background runtime worker and await cleanup on exit.
        """
        self.loop = asyncio.get_running_loop()
        # Worker callbacks schedule UI mutations; prompt-toolkit widgets are
        # accessed exclusively from the application event loop.
        def emit(event):
            """Schedule event rendering on the UI event loop from the worker thread."""
            self.loop.call_soon_threadsafe(self.event, event)
        approver = WorkApprover(
            lambda decision: self.loop.call_soon_threadsafe(self.request, decision),
            self.control)

        def execute():
            """Run the requested chat or task, capture errors, and schedule UI shutdown.
            """
            try:
                if self.task_id:
                    self.result = self.runtime.run_task(self.task_id, approver, emit,
                                                         self.control)
                else:
                    self.result = self.runtime.run(self.session_id, self.prompt,
                                                    approver, emit, self.control)
            except BaseException as error:
                self.error = error
            finally:
                self.loop.call_soon_threadsafe(self.app.exit)

        worker = None

        def start():
            """Launch the runtime worker once the prompt-toolkit application is ready.
            """
            nonlocal worker
            worker = asyncio.create_task(asyncio.to_thread(execute))

        try:
            await self.app.run_async(pre_run=start)
        finally:
            self.control.cancel()
            if worker:
                await worker
        if self.error:
            raise self.error
        return self.result

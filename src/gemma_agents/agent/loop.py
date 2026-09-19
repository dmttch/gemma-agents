"""Run bounded model/tool iterations with durable history and user steering."""

import json
from collections.abc import Callable

from gemma_agents.agent.context import ContextBuilder, ContextLimitError
from gemma_agents.control import RunControl
from gemma_agents.security.gateway import ToolGateway
from gemma_agents.sessions.manager import SessionManager


class AgentLoop:
    """Coordinate model responses, validated tool calls, and cooperative control."""

    def __init__(self, llm, gateway: ToolGateway, sessions: SessionManager,
                 context: ContextBuilder, max_steps: int = 20,
                 emit: Callable | None = None, control: RunControl | None = None):
        """Bind the model, session services, event sink, and per-run step limit."""
        self.llm = llm
        self.gateway = gateway
        self.sessions = sessions
        self.context = context
        self.max_steps = max_steps
        self.emit = emit or (lambda event: None)
        self.status = "completed"
        self.control = control or RunControl()
        self.steps = 0

    def _steering(self, session_id: str) -> bool:
        """Persist and emit queued user messages; report whether any were received."""
        messages = self.control.drain()
        for message in messages:
            self.sessions.save(session_id, {"role": "user", "content": message})
            self.emit({"type": "steering", "content": message})
        return bool(messages)

    def _repair_interrupted_calls(self, session_id: str) -> None:
        """Supply unknown-result placeholders for unanswered calls without replaying
        them.
        """
        history = self.sessions.history(session_id)
        if not history:
            return
        index = len(history) - 1
        while index >= 0 and history[index]["role"] == "tool":
            index -= 1
        if index < 0:
            return
        pending = history[index].get("tool_calls", [])
        # Results are stored in call order. Fill only the unanswered tail;
        # replaying a call could duplicate effects completed before a crash.
        answered = len(history) - index - 1
        for call in pending[answered:]:
            self.sessions.save(session_id, {
                "role": "tool", "tool_name": call["function"]["name"],
                "content": ("Tour interrompu. Résultat inconnu; "
                            "vérifier avant de réessayer."),
            })

    def run(self, session_id: str, user_message: str) -> str:
        """Persist a request and iterate until completion, blocking, or a step limit.

        User steering takes effect between actions. Interrupted calls are repaired
        in history before a new request, and identical consecutive tool calls
        are limited to two executions. Return the final assistant text.
        """
        if not user_message.strip() or len(user_message) > 16000:
            raise ValueError("Consigne vide ou trop longue (maximum 16000 caractères).")
        self._repair_interrupted_calls(session_id)
        self.sessions.save(session_id, {"role": "user", "content": user_message})
        previous = None
        repeats = 0
        for step in range(1, self.max_steps + 1):
            self.control.boundary()
            self._steering(session_id)
            self.steps = step
            self.emit({"type": "step", "step": step})
            try:
                messages = self.context.build(self.sessions.history(session_id))
            except ContextLimitError as error:
                return self._finish(session_id, str(error), "limited")
            if hasattr(self.llm, "chat_stream"):
                response = self.llm.chat_stream(
                    messages=messages, tools=self.gateway.registry.ollama_tools(),
                    emit=self._stream_event)
            else:
                response = self.llm.chat(messages=messages,
                                         tools=self.gateway.registry.ollama_tools())
            entry = response.message.model_dump(exclude_none=True)
            self.sessions.save(session_id, entry)
            calls = response.message.tool_calls or []
            if not calls:
                self.control.boundary()
                if self._steering(session_id):
                    continue
                self.status = "blocked" if self.gateway.blocked else "completed"
                result = response.message.content or ""
                self.emit({"type": "final", "content": result, "status": self.status})
                return result
            for call in calls:
                self.control.boundary()
                name = call.function.name
                arguments = dict(call.function.arguments)
                signature = json.dumps([name, arguments], sort_keys=True)
                # Steering invalidates the remaining planned actions, but each
                # skipped call still needs a tool result to keep history valid.
                if self.control.pending():
                    result = "Non exécuté : nouvelle consigne utilisateur en attente."
                elif signature == previous and repeats >= 2:
                    result = "Non exécuté : appel identique répété sans progrès."
                    self.status = "limited"
                else:
                    self.emit({"type": "tool_request", "name": name,
                               "arguments": arguments})
                    result = self.gateway.execute(name, arguments)
                repeats = repeats + 1 if signature == previous else 1
                previous = signature
                self.sessions.save(session_id, {
                    "role": "tool", "tool_name": name, "content": result,
                })
                self.emit({"type": "tool_result", "name": name, "content": result})
            if self.status == "limited":
                return self._finish(session_id, "Arrêt : appels répétés sans progrès.",
                                    "limited")
        self._steering(session_id)
        return self._finish(session_id,
                            f"Limite de {self.max_steps} étapes atteinte.", "limited")

    def _stream_event(self, event):
        """Check for cancellation before forwarding each streamed event."""
        self.control.check_cancelled()
        self.emit(event)

    def _finish(self, session_id: str, content: str, status: str) -> str:
        """Persist and emit a terminal assistant message with its run status."""
        self.status = status
        self.sessions.save(session_id, {"role": "assistant", "content": content})
        self.emit({"type": "final", "content": content, "status": status})
        return content

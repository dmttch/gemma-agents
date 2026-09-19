"""Provide terminal, deny-by-default, and remotely brokered tool approvals."""

import json
import threading
from typing import Protocol
from uuid import uuid4

import rich_click as click
from rich.console import Console


class Approver(Protocol):
    """Interface for deciding whether a specific sensitive tool call may proceed."""

    def confirm(self, tool_name: str, arguments: dict, reason: str) -> bool:
        """Return whether the named call and its arguments have been approved."""
        ...


class TerminalApprover:
    """Request one explicit terminal decision per sensitive tool call."""

    def confirm(self, tool_name: str, arguments: dict, reason: str) -> bool:
        """Display the call and ask for approval, denying cancelled or unavailable
        input.
        """
        console = Console()
        console.print(f"Action sensible : {tool_name}\n{reason}", markup=False)
        console.print(json.dumps(arguments, ensure_ascii=False, indent=2), markup=False)
        try:
            return click.confirm("Autoriser cette action ?", default=False)
        except (click.Abort, EOFError, KeyboardInterrupt):
            return False


class DenyApprover:
    """Refuse approval requests when no interactive authorization is available."""

    def confirm(self, tool_name: str, arguments: dict, reason: str) -> bool:
        """Deny the requested call unconditionally."""
        return False


class ApprovalBroker:
    """HTTP approval is bound to one immutable tool call, expires after 120s."""

    def __init__(self):
        """Initialize a lock-protected collection of pending approval requests."""
        self.pending = {}
        self.lock = threading.Lock()

    def request(self, session_id: str, tool: str, arguments: dict, reason: str) -> bool:
        """Publish a request and wait up to 120 seconds, then remove it from the broker.
        """
        approval_id = uuid4().hex
        item = {"id": approval_id, "session_id": session_id, "tool": tool,
                "arguments": arguments, "reason": reason,
                "event": threading.Event(), "approved": False}
        with self.lock:
            self.pending[approval_id] = item
        try:
            item["event"].wait(120)
            return item["approved"]
        finally:
            with self.lock:
                self.pending.pop(approval_id, None)

    def list(self) -> list[dict]:
        """Snapshot pending requests without exposing thread events or decision state.
        """
        with self.lock:
            return [{key: value for key, value in item.items()
                     if key not in {"event", "approved"}}
                    for item in self.pending.values()]

    def resolve(self, approval_id: str, approved: bool) -> None:
        """Set a pending request's decision once and wake its waiting worker."""
        with self.lock:
            item = self.pending[approval_id]
            if item["event"].is_set():
                raise ValueError("Cette approbation a déjà été traitée.")
            item["approved"] = approved
            item["event"].set()

    def close(self) -> None:
        """Wake all pending requests, leaving undecided calls denied by default."""
        with self.lock:
            for item in self.pending.values():
                item["event"].set()


class RemoteApprover:
    """Route a session's approval requests through the shared HTTP broker."""

    def __init__(self, broker: ApprovalBroker, session_id: str):
        """Bind a broker and session identifier for subsequent approval requests."""
        self.broker = broker
        self.session_id = session_id

    def confirm(self, tool_name: str, arguments: dict, reason: str) -> bool:
        """Block for the broker's decision on this session's specific tool call."""
        return self.broker.request(self.session_id, tool_name, arguments, reason)

"""Persist exact command grants and resolve session, task, and schedule scopes."""

import json
import shlex
from datetime import UTC, datetime, timedelta

from gemma_agents.memory.database import Database, now_iso


class PermissionStore:
    """Exact argv grants, scoped to a workspace and a human-selected lifetime."""

    def __init__(self, database: Database, workspace):
        """Bind grant storage to a canonical workspace identity."""
        self.database = database
        self.workspace = str(workspace.resolve())

    @staticmethod
    def key(command: str) -> str:
        """Normalize shell-style quoting into an exact serialized argument vector."""
        return json.dumps(shlex.split(command), ensure_ascii=False)

    def grant(self, scope: str, scope_id: str, command: str, hours: int = 8) -> int:
        """Store a policy-valid command grant for one scope with an expiring lifetime.
        """
        if scope not in {"session", "task", "schedule"} or not 1 <= hours <= 168:
            raise ValueError("Portée ou durée de permission invalide.")
        self.validate(command)
        expires = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
        with self.database.connect() as db:
            cursor = db.execute("INSERT INTO command_grants(workspace, scope, scope_id,"
                                "command, expires_at) VALUES (?, ?, ?, ?, ?)",
                                (self.workspace, scope, scope_id,
                                 self.key(command), expires))
        return cursor.lastrowid

    def validate(self, command: str) -> None:
        """Reject commands that cannot pass the runtime's shell policy."""
        from gemma_agents.security.policy import SecurityPolicy

        decision = SecurityPolicy(self.database.path.parent)._evaluate_shell(
            {"command": command})
        if not decision.allowed:
            raise ValueError(decision.reason)

    def allows(self, scopes: list[tuple[str, str]], command: str) -> bool:
        """Check live exact-command grants, including a task's originating schedule."""
        scopes = list(scopes)
        with self.database.connect() as db:
            for kind, identity in list(scopes):
                if kind == "task":
                    row = db.execute("SELECT s.schedule_id FROM task_sources s "
                                     "JOIN tasks t ON t.id=s.task_id WHERE t.id=? "
                                     "AND t.workspace=?", (identity, self.workspace)
                                     ).fetchone()
                    if row:
                        # Resolve inheritance at use time so revoking a schedule
                        # grant immediately affects its queued tasks as well.
                        scopes.append(("schedule", row[0]))
        return any((g["scope"], g["scope_id"]) in scopes
                   and g["command"] == self.key(command) for g in self.list())

    def list(self) -> list[dict]:
        """Return unexpired grants for this workspace in creation order."""
        with self.database.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM command_grants WHERE workspace=? AND expires_at>? "
                "ORDER BY id", (self.workspace, now_iso()))]

    def revoke(self, grant_id: int) -> None:
        """Delete a workspace-owned grant or raise ValueError if it is unknown."""
        with self.database.connect() as db:
            count = db.execute("DELETE FROM command_grants WHERE id=? AND workspace=?",
                               (grant_id, self.workspace)).rowcount
        if not count:
            raise ValueError("Permission inconnue dans ce workspace.")


class ScopedApprover:
    """Honor exact execution/check grants before consulting the fallback approver."""

    def __init__(self, store, scopes, fallback):
        """Retain the permission store, active scopes, and fallback decision provider.
        """
        self.store, self.scopes, self.fallback = store, scopes, fallback

    def confirm(self, tool_name, arguments, reason):
        """Reuse a matching command grant or request the fallback's explicit decision.
        """
        if tool_name in {"run_command", "run_check"} and self.store.allows(
                self.scopes, arguments["command"]):
            return True
        return self.fallback.confirm(tool_name, arguments, reason)

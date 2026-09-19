"""Persist session plans, queued tasks, and timezone-aware recurring schedules."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from croniter import croniter

from gemma_agents.memory.database import Database, now_iso
from gemma_agents.security.permissions import PermissionStore


class Planner:
    """Persist one ordered plan with per-step status for a session."""

    def __init__(self, database: Database, session_id: str):
        """Bind plan operations to a database and session identifier."""
        self.database = database
        self.session_id = session_id

    def set_plan(self, steps: list[str]) -> str:
        """Create or replace the plan for the current task before doing work.

        Args:
            steps: Between 1 and 20 concrete, verifiable steps.
        """
        if (not 1 <= len(steps) <= 20
                or any(not s.strip() or len(s) > 500 for s in steps)):
            raise ValueError("Fournis 1 à 20 étapes non vides, 500 caractères maximum.")
        plan = [{"step": step, "status": "pending"} for step in steps]
        with self.database.connect() as db:
            db.execute("INSERT INTO plans VALUES (?, ?) ON CONFLICT(session_id) "
                       "DO UPDATE SET payload = excluded.payload",
                       (self.session_id, json.dumps(plan, ensure_ascii=False)))
        return self.get_plan()

    def get_plan(self) -> str:
        """Read the current plan and progress."""
        with self.database.connect() as db:
            row = db.execute("SELECT payload FROM plans WHERE session_id = ?",
                             (self.session_id,)).fetchone()
        return row[0] if row else "[]"

    def update_plan(self, index: int, status: str) -> str:
        """Update one plan step after observing the result of work.

        Args:
            index: Zero-based step index.
            status: pending, running, completed or blocked.
        """
        if status not in {"pending", "running", "completed", "blocked"}:
            raise ValueError("Statut invalide.")
        plan = json.loads(self.get_plan())
        if not 0 <= index < len(plan):
            raise ValueError("Index invalide.")
        plan[index]["status"] = status
        with self.database.connect() as db:
            db.execute("UPDATE plans SET payload = ? WHERE session_id = ?",
                       (json.dumps(plan, ensure_ascii=False), self.session_id))
        return self.get_plan()


class TaskStore:
    """Manage workspace tasks and atomically materialize due schedule occurrences."""

    def __init__(self, database: Database, workspace: Path):
        """Bind tasks, schedules, and command grants to the same workspace identity."""
        self.database = database
        self.workspace = str(workspace.resolve())
        self.permissions = PermissionStore(database, workspace)

    def create(self, prompt: str, checks: list[str] | None = None) -> str:
        """Persist a bounded prompt and its fixed verification commands as a pending
        task.
        """
        if not prompt.strip() or len(prompt) > 16000:
            raise ValueError("Consigne vide ou trop longue (maximum 16000 caractères).")
        task_id = uuid4().hex[:12]
        checks = checks or []
        if len(checks) > 10 or any(not c.strip() or len(c) > 2000 for c in checks):
            raise ValueError("Au plus 10 commandes de vérification non vides.")
        with self.database.connect() as db:
            db.execute("INSERT INTO tasks(id, workspace, prompt, "
                       "created_at, updated_at) "
                       "VALUES (?, ?, ?, ?, ?)",
                       (task_id, self.workspace, prompt, now_iso(), now_iso()))
            db.execute("INSERT INTO task_checks VALUES (?, ?)",
                       (task_id, json.dumps(checks)))
        return task_id

    def checks(self, task_id: str) -> list[str]:
        """Return a workspace-owned task's saved verification commands."""
        self.get(task_id)
        with self.database.connect() as db:
            row = db.execute("SELECT commands FROM task_checks WHERE task_id=?",
                             (task_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def get(self, task_id: str) -> dict:
        """Return a task in this workspace or raise ValueError for an unknown
        identifier.
        """
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id = ? AND workspace = ?",
                             (task_id, self.workspace)).fetchone()
        if row is None:
            raise ValueError("Tâche inconnue dans ce workspace.")
        return dict(row)

    def list(self) -> list[dict]:
        """Return workspace tasks with the newest created first."""
        with self.database.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM tasks WHERE workspace = ? ORDER BY created_at DESC",
                (self.workspace,),
            )]

    def claim(self, task_id: str) -> dict:
        """Atomically move a pending task to running, rejecting duplicate claims."""
        with self.database.connect() as db:
            # Use one conditional update so competing workers cannot both claim
            # the same pending task after separate reads.
            count = db.execute(
                "UPDATE tasks SET status = 'running', updated_at = ? "
                "WHERE id = ? AND workspace = ? AND status = 'pending'",
                (now_iso(), task_id, self.workspace),
            ).rowcount
        if not count:
            raise ValueError("Tâche inconnue, déjà exécutée ou déjà en cours.")
        return self.get(task_id)

    def finish(self, task_id: str, session_id: str, status: str, result: str) -> None:
        """Persist a task's session, status, result, and update timestamp."""
        with self.database.connect() as db:
            db.execute("UPDATE tasks SET session_id = ?, status = ?, result = ?, "
                       "updated_at = ? WHERE id = ? AND workspace = ?",
                       (session_id, status, result, now_iso(), task_id, self.workspace))

    def recover(self, task_id: str) -> None:
        """Explicitly requeue an unfinished task while retaining its session and
        history.
        """
        # Explicit operator action, never automatic: completed side effects may exist.
        with self.database.connect() as db:
            count = db.execute(
                "UPDATE tasks SET status = 'pending', updated_at = ? "
                "WHERE id = ? AND workspace = ? AND status IN ('running','failed',"
                "'blocked','limited','interrupted')",
                (now_iso(), task_id, self.workspace),
            ).rowcount
        if not count:
            raise ValueError("Cette tâche ne peut pas être relancée.")

    @staticmethod
    def next_due(expression: str, timezone: str, now: datetime) -> str:
        """Return the next five-field cron occurrence in UTC, interpreted in its
        timezone.
        """
        if len(expression.split()) != 5:
            raise ValueError("Utilise un cron à cinq champs, minute à jour de semaine.")
        local = now.astimezone(ZoneInfo(timezone))
        due = croniter(expression, local).get_next(datetime)
        return due.astimezone(UTC).isoformat()

    def schedule(self, prompt: str, expression: str,
                 timezone: str = "Europe/Paris") -> str:
        """Persist an enabled recurring prompt and its next scheduled occurrence."""
        if not prompt.strip() or len(prompt) > 16000:
            raise ValueError("Consigne vide ou trop longue.")
        due = self.next_due(expression, timezone, datetime.now(UTC))
        schedule_id = uuid4().hex[:12]
        with self.database.connect() as db:
            db.execute("INSERT INTO schedules VALUES (?, ?, ?, ?, ?, ?, 1)",
                       (schedule_id, self.workspace, prompt, expression, timezone, due))
        return schedule_id

    def schedules(self) -> list[dict]:
        """Return all recurring schedules belonging to this workspace."""
        with self.database.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM schedules WHERE workspace = ?", (self.workspace,),
            )]

    def pause(self, schedule_id: str, paused: bool = True) -> None:
        """Disable or re-enable a workspace schedule without changing its next due time.
        """
        with self.database.connect() as db:
            count = db.execute("UPDATE schedules SET enabled = ? "
                               "WHERE id = ? AND workspace = ?",
                               (int(not paused), schedule_id, self.workspace)).rowcount
        if not count:
            raise ValueError("Planification inconnue.")

    def enqueue_due(self, now: datetime | None = None) -> list[str]:
        """Atomically enqueue one task per due schedule and advance its next run.

        Coalesce missed occurrences into one task per schedule. Compute the next
        due time from now rather than replaying every interval missed during downtime.
        """
        now = (now or datetime.now(UTC)).astimezone(UTC)
        ids = []
        with self.database.connect() as db:
            # Lock before reading due rows: insertion and next-run advancement
            # must commit together to prevent duplicate scheduled occurrences.
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM schedules WHERE workspace = ? "
                              "AND enabled = 1 AND next_run <= ?",
                              (self.workspace, now.isoformat())).fetchall()
            for row in rows:
                task_id = uuid4().hex[:12]
                db.execute("INSERT INTO tasks(id, workspace, prompt, created_at, "
                           "updated_at) VALUES (?, ?, ?, ?, ?)",
                           (task_id, self.workspace, row["prompt"],
                            now.isoformat(), now.isoformat()))
                db.execute("INSERT INTO task_sources VALUES (?, ?)",
                           (task_id, row["id"]))
                # Advance from now to coalesce missed ticks after downtime.
                due = self.next_due(row["cron"], row["timezone"], now)
                db.execute("UPDATE schedules SET next_run = ? WHERE id = ?",
                           (due, row["id"]))
                ids.append(task_id)
        return ids

"""Operator-only SQLite backup, restoration and scoped retention operations.

These functions are deliberately absent from the model's tool registry. Backups
contain conversations, grants and previous file contents: treat them as private.
"""

import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gemma_agents.locking import WorkspaceLease


def inspect_database(path: Path) -> int:
    """Return a compatible schema version after integrity and foreign-key checks."""
    from gemma_agents.memory.migrations import SCHEMA_VERSION

    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                 uri=True)) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if not 0 <= version <= SCHEMA_VERSION:
            raise ValueError(f"Schéma SQLite incompatible : {version}.")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Intégrité SQLite invalide.")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Références SQLite invalides.")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
        if not {"sessions", "messages"} <= tables:
            raise ValueError("Cette base ne contient pas de sessions Gemma Agents.")
    return version


def snapshot(source: Path, destination: Path) -> Path:
    """Create a consistent private backup without replacing any existing path.

    SQLite's backup API includes committed WAL contents. Publish via a hard link
    to an fsynced temporary file, so a concurrent destination creation cannot be
    overwritten and failed copies never masquerade as complete backups.
    """
    destination = destination.expanduser().absolute()
    descriptor, name = tempfile.mkstemp(prefix=".gemma-backup-",
                                        dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro",
                                     uri=True)) as src:
            with closing(sqlite3.connect(temporary)) as target, target:
                src.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
        inspect_database(temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def restore(source: Path, destination: Path) -> Path | None:
    """Restore while all runtimes sharing storage are stopped; retain a rescue copy.

    Refuse symlink destinations. Validate the input before touching the live
    database. Revoke restored command grants so old approvals cannot regain life.
    Workspace files and skills are never restored by this operation.
    """
    from gemma_agents.memory.migrations import migrate

    inspect_database(source)
    if source.resolve() == destination.resolve() or destination.is_symlink():
        raise ValueError("Source et destination doivent être des fichiers distincts.")
    lease = WorkspaceLease(destination.parent)
    temporary = None
    rescue = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".gemma-restore-",
                                            dir=destination.parent)
        os.close(descriptor)
        temporary = Path(name)
        temporary.unlink()
        snapshot(source, temporary)
        migrate(temporary)
        with closing(sqlite3.connect(temporary)) as db, db:
            db.execute("DELETE FROM command_grants")
            db.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
            db.execute("UPDATE tasks SET status='interrupted' WHERE status='running'")
        with closing(sqlite3.connect(temporary)) as db, db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("PRAGMA journal_mode=DELETE")
        if destination.exists():
            rescue = snapshot(destination, destination.with_name(
                f"{destination.name}.before-restore-{os.urandom(8).hex()}.backup"))
            with closing(sqlite3.connect(destination)) as db, db:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                db.execute("PRAGMA journal_mode=DELETE")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        return rescue
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
        lease.close()


class Retention:
    """Delete only operator-selected workspace data, preserving file contents."""

    def __init__(self, database, workspace: Path):
        """Bind maintenance operations to one workspace in a shared database."""
        self.database = database
        self.workspace = str(workspace.resolve())

    def delete_session(self, session_id: str) -> None:
        """Remove a session and associated tasks, history, edits and permissions.

        Reject active runs/tasks. All relational deletions commit together. This
        is logical deletion, not secure erasure from disks or existing backups.
        """
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND workspace=?",
                              (session_id, self.workspace)).fetchone():
                raise ValueError("Session inconnue dans ce workspace.")
            if (db.execute("SELECT 1 FROM runs WHERE session_id=? AND status='running'",
                           (session_id,)).fetchone() or db.execute(
                    "SELECT 1 FROM tasks WHERE session_id=? AND status='running'",
                    (session_id,)).fetchone()):
                raise ValueError("Reprends ou interromps la session avant suppression.")
            for table in ("task_sources", "task_checks"):
                db.execute(f"DELETE FROM {table} WHERE task_id IN "
                           "(SELECT id FROM tasks WHERE session_id=?)", (session_id,))
            db.execute("DELETE FROM command_grants WHERE workspace=? AND "
                       "((scope='session' AND scope_id=?) OR (scope='task' AND "
                       "scope_id IN (SELECT id FROM tasks WHERE session_id=?)))",
                       (self.workspace, session_id, session_id))
            db.execute("DELETE FROM verification_states WHERE verification_id IN "
                       "(SELECT id FROM verifications WHERE session_id=?)",
                       (session_id,))
            for table in ("tasks", "messages", "audit", "plans", "checkpoints",
                          "context_notes", "runs", "edits", "verifications",
                          "session_details"):
                db.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def prune_audit(self, days: int, *, apply: bool = False) -> int:
        """Count, or explicitly delete, workspace audit records older than days."""
        if days < 1:
            raise ValueError("La conservation doit être au moins un jour.")
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        where = ("created_at < ? AND session_id IN "
                 "(SELECT id FROM sessions WHERE workspace=?)")
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(f"SELECT COUNT(*) FROM audit WHERE {where}",
                               (cutoff, self.workspace)).fetchone()[0]
            if apply:
                db.execute(f"DELETE FROM audit WHERE {where}", (cutoff, self.workspace))
        return count

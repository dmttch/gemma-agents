"""Ordered, transactional SQLite migrations with pre-upgrade snapshots.

Versions 1–4 reconstruct the legacy additive schema. Version 0 also accepts the
original unversioned sessions/messages database. Never run an older runtime on a
newer schema. Each upgrade is one transaction and retains a coherent snapshot.
"""

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 5
MIGRATIONS = {
    1: """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            created_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id, id);
    """,
    2: """
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
            content TEXT NOT NULL, model TEXT NOT NULL,
            embedding TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plans (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id),
            payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
            prompt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            session_id TEXT REFERENCES sessions(id), result TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
            prompt TEXT NOT NULL, cron TEXT NOT NULL, timezone TEXT NOT NULL,
            next_run TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            tool TEXT NOT NULL, arguments TEXT NOT NULL,
            outcome TEXT NOT NULL, detail TEXT NOT NULL,
            created_at TEXT NOT NULL);
    """,
    3: """
        CREATE TABLE IF NOT EXISTS checkpoints (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id),
            payload TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS context_notes (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id),
            content TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            status TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, detail TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            path TEXT NOT NULL, before_text TEXT, after_text TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS task_checks (
            task_id TEXT PRIMARY KEY REFERENCES tasks(id),
            commands TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            command TEXT NOT NULL, status TEXT NOT NULL,
            output TEXT NOT NULL, created_at TEXT NOT NULL);
    """,
    4: """
        CREATE TABLE IF NOT EXISTS session_details (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id),
            title TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS command_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL,
            scope TEXT NOT NULL, scope_id TEXT NOT NULL,
            command TEXT NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS verification_states (
            verification_id INTEGER PRIMARY KEY REFERENCES verifications(id),
            before_hash TEXT, after_hash TEXT);
        CREATE TABLE IF NOT EXISTS task_sources (
            task_id TEXT PRIMARY KEY REFERENCES tasks(id),
            schedule_id TEXT NOT NULL REFERENCES schedules(id));
    """,
    5: """
        CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(workspace, status);
        CREATE INDEX IF NOT EXISTS idx_memories_workspace ON memories(workspace, model);
        CREATE INDEX IF NOT EXISTS idx_edits_session ON edits(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_audit_session ON audit(session_id, id);
    """,
}


def migrate(path: Path) -> None:
    """Upgrade atomically, preserving a snapshot of an existing older database.

    BEGIN IMMEDIATE serializes competing upgraders. Statements are executed
    individually because executescript would implicitly commit the transaction.
    The backup uses a separate read connection while the reserved write lock
    prevents other writers; backing up the active write connection would block.
    """
    from gemma_agents.storage import snapshot

    with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError(f"Schéma SQLite futur ({version}); mise à jour requise.")
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError(f"Schéma SQLite futur ({version}); mise à jour requise.")
        populated = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
        ).fetchone()
        if populated and version < SCHEMA_VERSION:
            destination = path.with_name(f"{path.name}.v{version}-{uuid4().hex}.backup")
            snapshot(path, destination)
        for target in range(version + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[target].split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(f"PRAGMA user_version={target}")
    # Journal mode cannot change inside the migration transaction.
    with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")

"""Maintain the SQLite schema and transactional session and audit storage."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


class Database:
    """Additive schema migration: existing V1 conversations remain readable."""

    def __init__(self, path: Path):
        """Create the storage directory and add missing schema objects without data
        loss.
        """
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, id);
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
                PRAGMA user_version=4;
            """)

    @contextmanager
    def connect(self):
        """Yield a row-based connection, committing on success and rolling back on
        error.

        Foreign keys are enabled for each connection. Close the connection after the
        transaction regardless of how the caller exits the context.
        """
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_session(self, workspace: Path) -> str:
        """Persist a session for the resolved workspace and return its identifier."""
        session_id = uuid4().hex[:12]
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                       (session_id, str(workspace.resolve()), now_iso()))
        return session_id

    def session_workspace(self, session_id: str) -> str | None:
        """Return the stored workspace path, or None for an unknown session."""
        with self.connect() as db:
            row = db.execute("SELECT workspace FROM sessions WHERE id = ?",
                             (session_id,)).fetchone()
        return row[0] if row else None

    def session_exists(self, session_id: str) -> bool:
        """Report whether a session identifier is present in the database."""
        return self.session_workspace(session_id) is not None

    def save_message(self, session_id: str, message: dict) -> None:
        """Append a JSON message to a session's durable conversation history."""
        with self.connect() as db:
            db.execute(
                "INSERT INTO messages(session_id, created_at, payload) "
                "VALUES (?, ?, ?)",
                (session_id, now_iso(), json.dumps(message, ensure_ascii=False)),
            )

    def load_messages(self, session_id: str) -> list[dict]:
        """Decode all session messages in insertion order."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def audit(self, session_id: str, tool: str, arguments: dict,
              outcome: str, detail: str) -> None:
        """Persist a tool outcome and arguments with a bounded detail excerpt."""
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit(session_id, tool, arguments, outcome, detail, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, tool, json.dumps(arguments, ensure_ascii=False),
                 outcome, detail[:4000], now_iso()),
            )

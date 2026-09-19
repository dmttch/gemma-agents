"""Persist bounded working summaries for resuming long-running sessions."""

import json

from gemma_agents.memory.database import Database, now_iso


class Checkpoints:
    """Store and retrieve one durable working summary per session."""

    def __init__(self, database: Database, session_id: str):
        """Bind checkpoint operations to a database and session identifier."""
        self.database = database
        self.session_id = session_id

    def save_checkpoint(self, objective: str, decisions: list[str],
                        next_steps: list[str]) -> str:
        """Save a concise durable working summary before a long task continues.

        Args:
            objective: Current user objective, up to 1000 characters.
            decisions: Confirmed decisions; never claim unobserved success.
            next_steps: Remaining work and unresolved questions.
        """
        payload = json.dumps(dict(objective=objective, decisions=decisions,
                                  next_steps=next_steps), ensure_ascii=False)
        if len(payload) > 6000 or len(objective) > 1000:
            raise ValueError("Point de reprise trop long (6000 caractères maximum).")
        with self.database.connect() as db:
            db.execute("INSERT INTO checkpoints VALUES (?, ?, ?) "
                       "ON CONFLICT(session_id) DO UPDATE SET "
                       "payload=excluded.payload, updated_at=excluded.updated_at",
                       (self.session_id, payload, now_iso()))
        return payload

    def get_checkpoint(self) -> str:
        """Read the durable working summary of this session."""
        with self.database.connect() as db:
            row = db.execute("SELECT payload FROM checkpoints WHERE session_id=?",
                             (self.session_id,)).fetchone()
        return row[0] if row else "{}"

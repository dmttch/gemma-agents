"""Manage workspace-owned conversations, metadata, forks, and Markdown exports."""

import json
from pathlib import Path

from gemma_agents.memory.database import Database


class SessionManager:
    """Validate workspace ownership before accessing session history or metadata."""

    def __init__(self, database: Database, workspace: Path):
        """Bind session operations to a database and canonical workspace."""
        self.database = database
        self.workspace = workspace.resolve()

    def create(self) -> str:
        """Create a session owned by this workspace and return its identifier."""
        return self.database.create_session(self.workspace)

    def resume(self, session_id: str) -> str:
        """Validate an existing session's workspace and return its identifier."""
        workspace = self.database.session_workspace(session_id)
        if workspace is None:
            raise ValueError(f"Session inconnue : {session_id}")
        if Path(workspace).resolve() != self.workspace:
            raise ValueError("Cette session appartient à un autre workspace.")
        return session_id

    def history(self, session_id: str) -> list[dict]:
        """Return all messages after verifying that the session belongs here."""
        self.resume(session_id)
        return self.database.load_messages(session_id)

    def save(self, session_id: str, message: dict) -> None:
        """Append a message and seed an unset title from the first nonempty user line.
        """
        self.resume(session_id)
        self.database.save_message(session_id, message)
        if message["role"] == "user" and message.get("content", "").strip():
            title = message["content"].strip().splitlines()[0][:80]
            with self.database.connect() as db:
                db.execute("INSERT INTO session_details(session_id, title) "
                           "VALUES (?, ?) "
                           "ON CONFLICT(session_id) DO UPDATE SET title=excluded.title "
                           "WHERE session_details.title=''", (session_id, title))

    def rename(self, session_id: str, title: str) -> None:
        """Set a nonempty session title of at most 120 characters."""
        self.resume(session_id)
        if not title.strip() or len(title) > 120:
            raise ValueError("Titre attendu : 1 à 120 caractères.")
        with self.database.connect() as db:
            db.execute("INSERT INTO session_details(session_id, title) VALUES (?, ?) "
                       "ON CONFLICT(session_id) DO UPDATE SET title=excluded.title",
                       (session_id, title.strip()))

    def set_model(self, session_id: str, model: str) -> None:
        """Persist the session's preferred model after validating ownership."""
        self.resume(session_id)
        with self.database.connect() as db:
            db.execute("INSERT INTO session_details(session_id, model) VALUES (?, ?) "
                       "ON CONFLICT(session_id) DO UPDATE SET model=excluded.model",
                       (session_id, model))

    def model(self, session_id: str) -> str:
        """Return the preferred model, or an empty string to use the runtime default."""
        self.resume(session_id)
        with self.database.connect() as db:
            row = db.execute("SELECT model FROM session_details WHERE session_id=?",
                             (session_id,)).fetchone()
        return row[0] if row else ""

    def fork(self, session_id: str) -> str:
        """Copy history and working metadata into a new session sharing the workspace.

        Reject active runs and add unknown-result placeholders for interrupted
        tool sequences. Copy plan, checkpoint, title, and model, leaving edit
        journals and command grants attached to their original session.
        """
        history = self.history(session_id)
        with self.database.connect() as db:
            active = db.execute("SELECT 1 FROM runs WHERE session_id=? "
                                "AND status='running'", (session_id,)).fetchone()
        if active:
            raise ValueError("Session inachevée : reprendre avant de la dupliquer.")
        index = len(history) - 1
        while index >= 0 and history[index]["role"] == "tool":
            index -= 1
        if index >= 0:
            # Complete the copied protocol sequence without rerunning unknown
            # effects in the shared workspace. The source history stays intact.
            for call in history[index].get("tool_calls", [])[len(history) - index - 1:]:
                history.append({"role": "tool", "tool_name": call["function"]["name"],
                                "content": "Appel interrompu, résultat inconnu. "
                                "Inspecter l'état réel avant toute répétition."})
        new_id = self.create()
        for message in history:
            self.save(new_id, message)
        self.save(new_id, {"role": "user", "content":
                          "Conversation dupliquée. Le workspace reste partagé ; "
                          "inspecte l'état réel avant toute modification."})
        with self.database.connect() as db:
            for table, columns in (("plans", "payload"),
                                   ("checkpoints", "payload, updated_at"),
                                   ("session_details", "title, model")):
                db.execute(f"INSERT OR REPLACE INTO {table} SELECT ?, {columns} "
                           f"FROM {table} WHERE session_id=?", (new_id, session_id))
        return new_id

    def export(self, session_id: str) -> str:
        """Return the session conversation and tool calls as Markdown text."""
        history = self.history(session_id)
        sections = [f"# Session {session_id}"]
        for message in history:
            body = message.get("content", "")
            if message.get("tool_calls"):
                body += "\n\n" + json.dumps(message["tool_calls"], ensure_ascii=False,
                                            indent=2)
            sections.append(f"## {message['role']}\n\n{body}")
        return "\n\n".join(sections) + "\n"

    def list(self, query: str = "") -> list[dict]:
        """List workspace sessions newest first, optionally matching titles or messages.
        """
        with self.database.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT s.*, COALESCE(d.title, '') AS title, "
                "COALESCE(d.model, '') AS model FROM sessions s "
                "LEFT JOIN session_details d ON d.session_id=s.id "
                "WHERE workspace=? AND (?='' OR instr(COALESCE(d.title,''), ?)>0 "
                "OR EXISTS (SELECT 1 FROM messages m WHERE m.session_id=s.id "
                "AND instr(m.payload, ?)>0)) ORDER BY created_at DESC",
                (str(self.workspace), query, query, query),
            )]

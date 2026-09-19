"""Record observed check results and detect whether they still match project files."""

from gemma_agents.memory.database import Database, now_iso
from gemma_agents.project import ProjectTools
from gemma_agents.tools.shell import ShellTools


class VerificationTools:
    """Persist command outcomes with before/after workspace fingerprints."""

    def __init__(self, database: Database, session_id: str, shell: ShellTools):
        """Bind a session's verification storage, command runner, and project discovery.
        """
        self.database = database
        self.session_id = session_id
        self.shell = shell
        self.project = ProjectTools(shell.runner.workspace)

    def run_check(self, command: str) -> str:
        """Run an approved verification command and persist its observed result.

        Args:
            command: Explicit test command, e.g. uv run pytest -q.
        """
        before = self.project.fingerprint()
        try:
            output = self.shell.run_command(command)
        except Exception as error:
            self.record(command, "error", str(error))
            raise
        status = "passed" if output.startswith("EXIT CODE: 0\n") else "failed"
        self.record(command, status, output, before, self.project.fingerprint())
        return f"VÉRIFICATION : {status}\n{output}"

    def record(self, command: str, status: str, output: str,
               before: str | None = None, after: str | None = None) -> None:
        """Persist bounded check output and optional before/after hashes in one
        transaction.
        """
        with self.database.connect() as db:
            cursor = db.execute("INSERT INTO verifications(session_id, command, status,"
                       "output, created_at) VALUES (?, ?, ?, ?, ?)",
                       (self.session_id, command, status, output[:40000], now_iso()))
            db.execute("INSERT INTO verification_states VALUES (?, ?, ?)",
                       (cursor.lastrowid, before, after))

    def list(self) -> list[dict]:
        """Return checks with current, stale, or unknown workspace freshness.

        A check is current only when its before and after hashes both equal the
        present fingerprint; checks that modify files do not qualify as current.
        """
        current = self.project.fingerprint()
        with self.database.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT v.*, s.before_hash, s.after_hash FROM verifications v "
                "LEFT JOIN verification_states s ON s.verification_id=v.id "
                "WHERE session_id=? ORDER BY id", (self.session_id,))]
        for row in rows:
            row["freshness"] = ("unknown" if not current or not row["before_hash"]
                                else "current" if row["before_hash"] ==
                                row["after_hash"] == current else "stale")
        return rows

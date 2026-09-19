"""Session edits with compare-before-restore, independent of Git state."""

import difflib
import os
import re
import tempfile
from pathlib import Path

from gemma_agents.memory.database import Database, now_iso


def atomic_write(path: Path, content: str) -> None:
    """Replace a UTF-8 file atomically, preserving its existing permission bits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # A sibling temporary file keeps replacement on the same filesystem, where
    # rename is atomic. Flush file data before exposing the new directory entry.
    descriptor, temporary = tempfile.mkstemp(prefix=".gemma-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def patch_text(content: str, patch: str, path: str) -> str:
    """Apply one strict unified diff; no fuzz, rename, or implicit file deletion."""
    lines = patch.splitlines(keepends=True)
    if len(lines) < 3 or not lines[0].startswith("--- "):
        raise ValueError("Patch unifié attendu : --- a/chemin puis +++ b/chemin.")
    if (lines[0].rstrip("\r\n") != f"--- a/{path}"
            or lines[1].rstrip("\r\n") != f"+++ b/{path}"):
        raise ValueError("Les deux chemins du patch doivent correspondre au fichier.")
    source = content.splitlines(keepends=True)
    result = []
    position = 0
    index = 2
    while index < len(lines):
        match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n?",
                             lines[index])
        if not match:
            raise ValueError("En-tête de bloc de patch invalide.")
        old_line, old_count, new_line, new_count = match.groups()
        old_count = int(old_count) if old_count is not None else 1
        new_count = int(new_count) if new_count is not None else 1
        # Nonempty unified-diff ranges are one-based. An empty range instead
        # names the insertion boundary, so subtracting one would shift it.
        start = int(old_line) - (1 if old_count else 0)
        if not position <= start <= len(source):
            raise ValueError("Position de patch invalide.")
        result.extend(source[position:start])
        if int(new_line) - (1 if new_count else 0) != len(result):
            raise ValueError("Position de destination incohérente.")
        position = start
        index += 1
        removed = added = 0
        while index < len(lines) and not lines[index].startswith("@@ "):
            line = lines[index]
            if not line or line[0] not in " +-":
                raise ValueError("Ligne de patch invalide.")
            kind, value = line[0], line[1:]
            index += 1
            # This marker describes the preceding source/destination line; it is
            # metadata, not a line to include in the resulting file.
            if index < len(lines) and lines[index].startswith("\\ No newline"):
                value = value.rstrip("\n")
                index += 1
            if kind in " -":
                if position >= len(source) or source[position] != value:
                    raise ValueError("Le fichier a changé : "
                                     "contexte du patch différent.")
                position += 1
                removed += 1
            if kind in " +":
                result.append(value)
                added += 1
        if (removed, added) != (old_count, new_count):
            raise ValueError("Nombre de lignes du patch incohérent.")
    return "".join([*result, *source[position:]])


class ChangeStore:
    """Journal session edits and review or undo them only against matching content."""

    def __init__(self, database: Database, session_id: str, workspace: Path,
                 preview: bool = False):
        """Bind an edit journal to a session and optionally stage proposals only."""
        self.database = database
        self.session_id = session_id
        self.workspace = workspace.resolve()
        self.preview = preview

    def write(self, path: Path, content: str) -> None:
        """Journal a bounded text edit before applying it atomically.

        In preview mode, save a proposal and raise ValueError to signal that the
        file was not changed. A pending record survives interrupted application.
        """
        relative = str(path.relative_to(self.workspace))
        if path.exists() and path.stat().st_size > 300000:
            raise ValueError("Fichier trop volumineux pour le journal d'éditions.")
        before = path.read_bytes().decode("utf-8") if path.exists() else None
        if before == content:
            return
        if max(len(content), len(before or "")) > 300000:
            raise ValueError("Édition trop volumineuse (300000 caractères maximum).")
        with self.database.connect() as db:
            cursor = db.execute("INSERT INTO edits(session_id, path, before_text, "
                                "after_text, status, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?)",
                                (self.session_id, relative, before, content,
                                 "proposed" if self.preview else "pending", now_iso()))
            edit_id = cursor.lastrowid
        if self.preview:
            raise ValueError(f"Édition {edit_id} préparée, non appliquée. "
                             "L'utilisateur doit la valider avec /review.")
        # Persist intent before writing. If application is interrupted, pending
        # records retain both versions for inspection rather than claiming success.
        atomic_write(path, content)
        with self.database.connect() as db:
            db.execute("UPDATE edits SET status='applied' WHERE id=?", (edit_id,))

    def list(self) -> list[dict]:
        """Return this session's edits with the most recent first."""
        with self.database.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM edits WHERE session_id=? ORDER BY id DESC",
                (self.session_id,))]

    def diff(self) -> str:
        """Render unified diffs for edits that remain pending, proposed, or applied."""
        chunks = []
        for edit in reversed(self.list()):
            if edit["status"] in {"undone", "rejected", "reviewed"}:
                continue
            chunks.append(f"Édition {edit['id']} · {edit['status']}\n")
            chunks.extend(difflib.unified_diff(
                (edit["before_text"] or "").splitlines(keepends=True),
                edit["after_text"].splitlines(keepends=True),
                fromfile=f"a/{edit['path']}", tofile=f"b/{edit['path']}"))
        return "".join(chunks) or "Aucune édition enregistrée pour cette session."

    def get(self, edit_id: int) -> dict:
        """Return an edit owned by this session or raise ValueError."""
        for edit in self.list():
            if edit["id"] == edit_id:
                return edit
        raise ValueError("Édition inconnue dans cette session.")

    def hunks(self, edit_id: int) -> list[dict]:
        """Return changed opcode ranges with stable indices into the full diff."""
        edit = self.get(edit_id)
        before = (edit["before_text"] or "").splitlines(keepends=True)
        after = edit["after_text"].splitlines(keepends=True)
        return [{"index": index, "old_start": a, "old_end": b,
                 "new_start": c, "new_end": d,
                 "diff": "".join([*("-" + line for line in before[a:b]),
                                    *("+" + line for line in after[c:d])])}
                for index, (kind, a, b, c, d) in enumerate(
                    difflib.SequenceMatcher(None, before, after, autojunk=False)
                    .get_opcodes()) if kind != "equal"]

    def review(self, edit_id: int, action: str,
                selected: list[int] | None = None) -> str:
        """Accept or undo selected hunks, or reject an unapplied proposal.

        Hunk indices come from hunks(); None selects all changed hunks. Refuse
        stale content or replaced paths before writing. Partial changes create
        a new journal entry so that the resulting write can itself be undone.
        """
        edit = self.get(edit_id)
        if action == "reject" and edit["status"] == "proposed":
            with self.database.connect() as db:
                db.execute("UPDATE edits SET status='rejected' WHERE id=?", (edit_id,))
            return f"Proposition {edit_id} rejetée ; fichier inchangé."
        if action not in {"accept", "undo"}:
            raise ValueError("Action attendue : accept, reject ou undo.")
        if ((action == "accept" and edit["status"] != "proposed")
                or (action == "undo" and edit["status"] != "applied")):
            raise ValueError("Action incompatible avec l'état de l'édition.")
        path = self.workspace / edit["path"]
        if path.resolve() != path or not path.is_relative_to(self.workspace):
            raise ValueError("Chemin modifié ; revue refusée.")
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError("Fichier remplacé ou lié ; revue refusée.")
        current = path.read_bytes().decode("utf-8") if path.exists() else None
        expected = edit["before_text"] if action == "accept" else edit["after_text"]
        # Compare before changing anything so intervening user edits survive.
        if current != expected:
            raise ValueError("Le fichier a changé ; revue refusée pour le préserver.")
        hunks = self.hunks(edit_id)
        valid = {h["index"] for h in hunks}
        chosen = valid if selected is None else set(selected)
        if not chosen or not chosen <= valid:
            raise ValueError("Sélection de blocs vide ou invalide.")
        if action == "undo" and chosen == valid:
            return self.undo(edit_id)
        before = (edit["before_text"] or "").splitlines(keepends=True)
        after = edit["after_text"].splitlines(keepends=True)
        target = before if action == "accept" else after
        # Apply from the end so earlier slice coordinates remain valid when
        # selected hunks insert or remove different numbers of lines.
        for hunk in reversed(hunks):
            if hunk["index"] in chosen:
                if action == "accept":
                    target[hunk["old_start"]:hunk["old_end"]] = after[
                        hunk["new_start"]:hunk["new_end"]]
                else:
                    target[hunk["new_start"]:hunk["new_end"]] = before[
                        hunk["old_start"]:hunk["old_end"]]
        ChangeStore(self.database, self.session_id, self.workspace).write(
            path, "".join(target))
        if action == "accept":
            with self.database.connect() as db:
                db.execute("UPDATE edits SET status='reviewed' WHERE id=?", (edit_id,))
        return (f"Édition {edit_id} : {len(chosen)} bloc(s) traité(s). "
                "Les blocs non sélectionnés restent inchangés dans le fichier.")

    def undo(self, edit_id: int | None = None) -> str:
        """Undo a selected or latest applied edit only if its recorded output remains.

        Delete files created by the edit; otherwise restore the original text.
        Raise ValueError if the path or content changed since the edit.
        """
        edits = [e for e in self.list() if e["status"] == "applied"
                 and (edit_id is None or e["id"] == edit_id)]
        if not edits:
            raise ValueError("Aucune édition applicable à annuler.")
        edit = edits[0]
        path = self.workspace / edit["path"]
        resolved = path.resolve()
        if (resolved != path or not resolved.is_relative_to(self.workspace)
                or not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError("Chemin modifié ou fichier absent : annulation refusée.")
        if path.read_bytes().decode("utf-8") != edit["after_text"]:
            raise ValueError("Le fichier a changé depuis cette édition : "
                             "annulation refusée pour préserver tes modifications.")
        if edit["before_text"] is None:
            path.unlink()
        else:
            atomic_write(path, edit["before_text"])
        with self.database.connect() as db:
            db.execute("UPDATE edits SET status='undone' WHERE id=?", (edit["id"],))
        return f"Édition {edit['id']} annulée : {edit['path']}"

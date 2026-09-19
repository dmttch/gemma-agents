"""Provide bounded workspace text reads and exact, optionally journaled edits."""

import json
from pathlib import Path

from gemma_agents.changes import ChangeStore, atomic_write, patch_text
from gemma_agents.tools.results import ToolFailure


class FileSystemTools:
    """Expose workspace file tools with path checks and optional edit journaling."""

    MAX_READ_SIZE = 300_000

    def __init__(self, workspace: Path, changes: ChangeStore | None = None):
        """Resolve the workspace boundary and retain an optional change journal."""
        self.workspace = workspace.resolve()
        self.changes = changes

    def _write(self, path: Path, content: str) -> None:
        """Write through the change journal when present, otherwise replace atomically.
        """
        if self.changes:
            self.changes.write(path, content)
        else:
            atomic_write(path, content)

    def _safe_path(self, relative_path: str) -> Path:
        """Resolve a path and reject targets outside the authorized workspace."""

        path = (self.workspace / relative_path).resolve()

        if path != self.workspace and self.workspace not in path.parents:
            raise PermissionError(
                "Le chemin sort du workspace autorisé."
            )

        return path

    def list_files(self,
                   directory: str = ".",
                   recursive: bool = False) -> str:
        """
        List files and directories inside the workspace.

        Args:
            directory: Directory relative to the workspace.
            recursive: Recursively list subdirectories.

        Returns:
            A textual list of files and directories.
        """

        root = self._safe_path(directory)

        if not root.exists():
            return ToolFailure(f"Dossier inexistant : {directory}")

        if not root.is_dir():
            return ToolFailure(f"Ce chemin n'est pas un dossier : {directory}")

        iterator = (
            root.rglob("*")
            if recursive
            else root.iterdir()
        )

        results = []

        for item in iterator:

            try:
                relative = item.relative_to(self.workspace)
            except ValueError:
                continue

            kind = "DIR " if item.is_dir() else "FILE"

            results.append(
                f"{kind} {relative}"
            )

            if len(results) >= 500:
                results.append(
                    "... résultat tronqué ..."
                )
                break

        if not results:
            return "Dossier vide."

        return "\n".join(results)

    def read_file(self, path: str, start_line: int = 1,
                  end_line: int = 200) -> str:
        """
        Read a UTF-8 text file from the workspace.

        Args:
            path: File path relative to the workspace.
            start_line: First line to read (one-based).
            end_line: Last line, inclusive; at most 500 lines per call.

        Returns:
            Navigation metadata, then the excerpt exactly as stored in the file:
            quotes, backslashes and line endings are never escaped a second time.
        """

        file_path = self._safe_path(path)

        if not file_path.exists():
            return ToolFailure(f"Fichier inexistant : {path}")

        if not file_path.is_file():
            return ToolFailure(f"Ce chemin n'est pas un fichier : {path}")

        if start_line < 1 or end_line < start_line or end_line - start_line >= 500:
            raise ValueError("Plage invalide : 1 à 500 lignes par lecture.")

        try:
            lines = []
            size = 0
            truncated = False
            last_line = start_line - 1
            partial_line = False
            with file_path.open(encoding="utf-8", newline="") as stream:
                for number, line in enumerate(stream, 1):
                    if number > end_line:
                        truncated = True
                        break
                    if number >= start_line:
                        excerpt = line[:16000 - size]
                        # Distinguish a cut-off line from omitted later lines so
                        # callers know whether the last returned line is complete.
                        partial_line = len(excerpt) != len(line)
                        lines.append(excerpt)
                        size += len(excerpt)
                        last_line = number
                        if size >= 16000:
                            truncated = partial_line or bool(stream.read(1))
                            break
            return json.dumps({"path": path, "start_line": start_line,
                               "end_line": last_line, "content": "".join(lines),
                               "truncated": truncated, "partial_line": partial_line},
                              ensure_ascii=False)

        except UnicodeDecodeError:
            return ToolFailure(
                "Impossible de lire ce fichier comme "
                "texte UTF-8."
            )

    def write_file(self,
                   path: str,
                   content: str,
                   overwrite: bool = False) -> str:
        """
        Write a UTF-8 text file inside the workspace.

        Args:
            path: File path relative to the workspace.
            content: Content to write.
            overwrite: Allow replacing an existing file.

        Returns:
            Result of the operation.
        """

        file_path = self._safe_path(path)

        if file_path.exists() and not overwrite:
            return ToolFailure(
                f"Refus : {path} existe déjà. "
                "Utilise overwrite=true si nécessaire."
            )

        if file_path.exists() and file_path.stat().st_nlink > 1:
            raise PermissionError("Écriture refusée : fichier avec liens physiques.")

        file_path.parent.mkdir(parents=True, exist_ok=True)

        self._write(file_path, content)

        return f"Fichier écrit : {path}"

    def replace_text(self,
                     path: str,
                     old_text: str,
                     new_text: str) -> str:
        """
        Replace an exact text fragment inside a file.

        Args:
            path: File path relative to the workspace.
            old_text: Exact existing text to replace.
            new_text: Replacement text.

        Returns:
            Result of the edit.
        """

        file_path = self._safe_path(path)

        if not file_path.exists():
            return ToolFailure(f"Fichier inexistant : {path}")

        if not old_text:
            raise ValueError("Le fragment à remplacer doit être non vide.")
        if file_path.stat().st_nlink > 1:
            raise PermissionError("Écriture refusée : fichier avec liens physiques.")
        if file_path.stat().st_size > self.MAX_READ_SIZE:
            raise ValueError("Fichier trop volumineux pour replace_text.")

        content = file_path.read_bytes().decode("utf-8")

        occurrences = content.count(old_text)

        if occurrences == 0:
            return ToolFailure(
                "Modification impossible : "
                "le texte recherché n'existe pas."
            )

        if occurrences > 1:
            return ToolFailure(
                "Modification refusée : "
                f"{occurrences} occurrences trouvées. "
                "Fournis un fragment plus précis."
            )

        updated = content.replace(old_text, new_text, 1)

        self._write(file_path, updated)

        return f"Fichier modifié : {path}"

    def apply_patch(self, path: str, patch: str) -> str:
        """Apply an exact single-file unified diff, recorded for inspection and undo.

        Args:
            path: Existing workspace file, relative path.
            patch: Unified diff with --- a/path, +++ b/path and @@ hunks.
        """
        target = self._safe_path(path)
        if not target.is_file() or target.stat().st_size > self.MAX_READ_SIZE:
            raise ValueError("Fichier absent ou trop volumineux.")
        if target.stat().st_nlink != 1:
            raise PermissionError("Écriture refusée : fichier avec liens physiques.")
        content = target.read_bytes().decode("utf-8")
        self._write(target, patch_text(content, patch, path))
        return f"Patch appliqué : {path}"

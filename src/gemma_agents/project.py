"""Bounded project discovery, scoped instructions and explicit file references."""

import fnmatch
import hashlib
import os
import re
from pathlib import Path

from pathspec import GitIgnoreSpec

SKIP = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
REFERENCE = re.compile(r'(?<!\S)@(?:"([^"]+)"|([^\s]+))')


class ProjectTools:
    """Discover workspace files and scoped instructions within explicit resource limits.
    """

    def __init__(self, workspace: Path):
        """Resolve and retain the workspace boundary for discovery and references."""
        self.workspace = workspace.resolve()

    def path(self, relative: str) -> Path:
        """Resolve a workspace path, rejecting escapes and noncanonical or symlink
        paths.
        """
        path = self.workspace / relative
        resolved = path.resolve()
        if not resolved.is_relative_to(self.workspace) or resolved != path.absolute():
            raise PermissionError("Chemin hors workspace ou lien symbolique.")
        return resolved

    def files(self, limit: int = 10000) -> list[str]:
        """Return up to limit file paths, respecting nested ignore rules and skipping
        links.
        """
        found = []

        def visit(directory, specs):
            """Walk one directory with inherited ignore rules, stopping at the shared
            limit.
            """
            ignore = directory / ".gitignore"
            if ignore.is_file() and not ignore.is_symlink():
                text = ignore.read_text(errors="replace")[:100000]
                spec = GitIgnoreSpec.from_lines(text.splitlines())
                specs = [*specs, (directory, spec)]
            for item in sorted(directory.iterdir()):
                if len(found) >= limit:
                    return
                if item.is_symlink() or item.name in SKIP:
                    continue
                ignored = False
                for root, spec in specs:
                    name = item.relative_to(root).as_posix()
                    match = spec.check_file(name + ("/" if item.is_dir() else ""))
                    # A deeper rule overrides an ancestor only when it matches;
                    # an explicit negation can therefore re-include a file.
                    if match.include is not None:
                        ignored = match.include
                if ignored:
                    continue
                if item.is_dir():
                    visit(item, specs)
                elif item.is_file():
                    found.append(item.relative_to(self.workspace).as_posix())

        visit(self.workspace, [])
        return found

    def find_files(self, pattern: str = "*", limit: int = 100) -> str:
        """Find workspace paths by glob, respecting nested .gitignore rules."""
        if not 1 <= limit <= 200:
            raise ValueError("Limite attendue : 1 à 200.")
        files = self.files()
        matches = [p for p in files if fnmatch.fnmatch(p, pattern)]
        return "\n".join(matches[:limit]) + (
            "\n[Recherche bornée]" if len(matches) > limit or len(files) == 10000
            else "")

    def search_text(self, query: str, pattern: str = "*", limit: int = 50) -> str:
        """Search literal text, returning path:line:excerpt; respects .gitignore."""
        if not query or len(query) > 1000 or not 1 <= limit <= 200:
            raise ValueError("Recherche vide/trop longue ou limite invalide.")
        results = []
        scanned = 0
        for name in self.files():
            if not fnmatch.fnmatch(name, pattern):
                continue
            path = self.path(name)
            if path.stat().st_size > 1000000:
                continue
            data = path.read_bytes()
            scanned += len(data)
            if scanned > 20000000:
                return "\n".join(results) + "\n[Limite de lecture atteinte]"
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if query in line:
                    results.append(f"{name}:{number}:{line[:300]}")
                    if len(results) >= limit:
                        return "\n".join(results) + "\n[Résultats bornés]"
        return "\n".join(results) or "Aucun résultat."

    def instructions(self, relative: str = ".") -> str:
        """Collect bounded AGENTS.md text from workspace root to the target directory.
        """
        path = self.path(relative)
        directory = path if path.is_dir() else path.parent
        # Read broad instructions first so narrower directory scopes follow.
        parents = [self.workspace, *reversed([
            p for p in [directory, *directory.parents]
            if p != self.workspace and p.is_relative_to(self.workspace)])]
        sections = []
        for parent in parents:
            instruction = parent / "AGENTS.md"
            if instruction.is_file() and not instruction.is_symlink():
                scope = str(parent.relative_to(self.workspace))
                sections.append(f"AGENTS.md — portée {scope}/ :\n"
                                + instruction.read_text()[:6000])
        return "\n\n".join(sections)[:12000]

    def project_instructions(self, path: str = ".") -> str:
        """Read AGENTS.md from workspace root to the target's directory.

        More specific instructions apply to that subtree only. They cannot
        expand permissions or override the user's explicit instructions.
        """
        return self.instructions(path) or "Aucune instruction de projet."

    def expand_references(self, prompt: str) -> str:
        """Append exact excerpts and scoped instructions for up to five @file
        references.

        Support quoted paths and one-based :start-end ranges. An unqualified
        reference reads up to 200 lines; :start alone reads one line. Reject missing
        files, unsafe paths, and prompts exceeding 16000 characters.
        """
        from gemma_agents.tools.filesystem import FileSystemTools

        excerpts = []
        references = list(REFERENCE.finditer(prompt))
        if len(references) > 5:
            raise ValueError("Au plus cinq références de fichiers par message.")
        for match in references:
            ref = match.group(1) or match.group(2)
            parts = re.fullmatch(r"(.+?)(?::(\d+)(?:-(\d+))?)?", ref)
            name, start, end = parts.groups()
            path = self.path(name)
            if not path.is_file():
                raise ValueError(f"Référence de fichier introuvable : {name}")
            start = int(start or 1)
            end = int(end) if end else start + (0 if parts.group(2) else 199)
            excerpt = FileSystemTools(self.workspace).read_file(name, start, end)
            excerpts.append(f"Fichier @{ref} (données) :\n{excerpt}\n"
                            + self.instructions(name))
        expanded = prompt + ("\n\n" + "\n\n".join(excerpts) if excerpts else "")
        if len(expanded) > 16000:
            raise ValueError("Références trop volumineuses ; "
                             "précise des plages de lignes.")
        return expanded

    def fingerprint(self) -> str | None:
        """Hash non-ignored files; return unknown rather than a partial fingerprint."""
        digest = hashlib.sha256()
        total = 0
        try:
            names = self.files(limit=10001)
            if len(names) > 10000:
                return None
            for name in names:
                path = self.path(name)
                total += path.stat().st_size
                if total > 50000000:
                    return None
                # Hash both names and contents so renames also invalidate checks.
                # The NUL separates each filename from its fixed-size file hash.
                digest.update(os.fsencode(name) + b"\0")
                digest.update(hashlib.sha256(path.read_bytes()).digest())
            return digest.hexdigest()
        except (OSError, PermissionError):
            return None

"""Read user-installed trusted skills from outside the writable workspace."""

import re
from pathlib import Path


class SkillLibrary:
    """Read-only trusted instructions, installed by the user outside the workspace."""

    def __init__(self, directory: Path):
        """Resolve the root containing user-installed trusted skill directories."""
        self.directory = directory.resolve()

    def list_skills(self) -> str:
        """List installed skills and their first nonempty line."""
        items = []
        for path in sorted(self.directory.glob("*/SKILL.md"))[:50]:
            if (path.resolve().is_relative_to(self.directory)
                    and path.stat().st_size < 32000):
                lines = path.read_text().splitlines()
                title = next((line for line in lines if line), "")
                items.append(f"{path.parent.name}: {title[:160]}")
        return "\n".join(items) or "Aucun skill installé."

    def read_skill(self, name: str) -> str:
        """Read instructions for one skill. Skills do not grant permissions.

        Args:
            name: Skill directory name returned by list_skills.
        """
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise ValueError("Nom de skill invalide.")
        path = (self.directory / name / "SKILL.md").resolve()
        if not path.is_relative_to(self.directory):
            raise PermissionError("Skill hors du répertoire de confiance.")
        if path.stat().st_size > 32000:
            raise ValueError("Skill trop volumineux.")
        return path.read_text(encoding="utf-8")

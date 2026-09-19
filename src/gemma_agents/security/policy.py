"""Decide tool eligibility and approval requirements before any execution."""

import shlex
from dataclasses import dataclass
from pathlib import Path

from gemma_agents.tools.registry import ToolSpec


@dataclass(frozen=True)
class Decision:
    """Immutable policy result separating eligibility from approval requirements."""

    allowed: bool
    requires_approval: bool
    reason: str


class SecurityPolicy:
    """Apply review-mode restrictions, tool risk levels, and shell command rules."""

    SHELL_ALLOWED_PROGRAMS = {"uv", "ls", "pwd", "head", "tail"}
    FORBIDDEN_SHELL_FRAGMENTS = ("|", ";", "&", ">", "<", "`", "$(", "\n")

    def __init__(self, workspace: Path, review_mode: bool = False):
        """Resolve the workspace and select whether edits are being prepared for review.
        """
        self.workspace = workspace.resolve()
        self.review_mode = review_mode

    def evaluate(self, spec: ToolSpec, arguments: dict) -> Decision:
        """Determine whether a tool is allowed and whether human approval is needed."""
        # Review restrictions take precedence over even previously granted commands.
        if self.review_mode and spec.risk == "high":
            return Decision(False, False, "Mode préparation : exécution et Git en "
                            "écriture désactivés jusqu'à /review off.")
        if spec.name in {"run_command", "start_process", "run_check"}:
            return self._evaluate_shell(arguments)
        if spec.risk in {"low", "medium"}:
            return Decision(True, False, "Action limitée au workspace et à la session.")
        if spec.risk == "high":
            return Decision(True, True, "Action sensible : validation humaine requise.")
        return Decision(False, False, "Niveau de risque inconnu.")

    def _evaluate_shell(self, arguments: dict) -> Decision:
        """Require an allowed program and reject shell constructs before approval."""
        command = arguments.get("command", "").strip()
        if not command:
            return Decision(False, False, "Commande vide.")
        if any(part in command for part in self.FORBIDDEN_SHELL_FRAGMENTS):
            return Decision(False, False, "Construction shell interdite.")
        try:
            parsed = shlex.split(command)
        except ValueError as error:
            return Decision(False, False, str(error))
        if not parsed or parsed[0] not in self.SHELL_ALLOWED_PROGRAMS:
            return Decision(False, False, "Programme non autorisé. Utilise uv run.")
        return Decision(True, True, "Exécution d'un processus local dans le sandbox.")

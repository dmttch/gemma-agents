"""Validate, authorize, execute, and audit every model-requested tool call."""

from gemma_agents.memory.database import Database
from gemma_agents.project import ProjectTools
from gemma_agents.security.approvals import Approver
from gemma_agents.security.policy import SecurityPolicy
from gemma_agents.tools.registry import ToolRegistry


class ToolGateway:
    """Enforce tool policy and track unresolved failures across an agent turn."""

    def __init__(self, registry: ToolRegistry, policy: SecurityPolicy,
                 approver: Approver, database: Database, session_id: str):
        """Bind execution services and initialize instruction and failure tracking."""
        self.registry = registry
        self.policy = policy
        self.approver = approver
        self.database = database
        self.session_id = session_id
        self.denied = False
        self.failed_tools = set()
        self.instruction_texts = set()

    @property
    def blocked(self):
        """Report any denied action or tool error that has not since recovered."""
        return self.denied or bool(self.failed_tools)

    def execute(self, name: str, arguments: dict) -> str:
        """Validate and audit a tool call, returning bounded output or an error message.

        Defer file access once for each unseen instruction text so the model can
        read scoped guidance first. Evaluate policy before consulting approvals.
        A later successful call clears that tool's error; denials remain recorded.
        """
        outcome = "error"
        try:
            arguments = self.registry.validate(name, arguments)
            if name in {"write_file", "replace_text", "apply_patch", "read_file"}:
                instructions = ProjectTools(self.policy.workspace).instructions(
                    arguments["path"])
                if instructions and instructions not in self.instruction_texts:
                    self.instruction_texts.add(instructions)
                    result = ("Instructions du projet à prendre en compte avant "
                              "cet appel (non exécuté). Réessaie après lecture :\n"
                              + instructions)
                    self.database.audit(self.session_id, name, arguments,
                                        "deferred", result)
                    return result
            decision = self.policy.evaluate(self.registry.get(name), arguments)
            if not decision.allowed:
                outcome = "denied"
                result = f"ACTION REFUSÉE : {decision.reason}"
            elif decision.requires_approval and not self.approver.confirm(
                    name, arguments, decision.reason):
                outcome = "denied"
                result = "ACTION REFUSÉE : approbation humaine absente ou refusée."
            else:
                result = str(self.registry.execute(name, arguments))
                outcome = "executed"
        except Exception as error:
            result = f"ERREUR OUTIL : {type(error).__name__}: {error}"
        # Denials remain sticky for the turn; a corrected successful invocation
        # can clear a validation/execution error for that tool.
        self.denied |= outcome == "denied"
        if outcome == "error":
            self.failed_tools.add(name)
        elif outcome == "executed":
            self.failed_tools.discard(name)
        self.database.audit(self.session_id, name, arguments, outcome, result)
        return result[:40_000]

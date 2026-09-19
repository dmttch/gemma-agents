"""Load and validate runtime settings and workspace/storage boundaries."""

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration, resource limits, and trusted storage paths."""

    model: str
    ollama_host: str
    workspace: Path
    storage_dir: Path
    database_path: Path
    system_prompt_path: Path
    max_agent_steps: int = 20
    command_timeout: int = 120
    keep_alive: str = "15m"
    embedding_model: str = "embeddinggemma"
    sandbox: str = "required"
    skills_dir: Path | None = None
    searxng_url: str = ""
    api_token: str = ""
    context_chars: int = 60_000
    repair_attempts: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        """Load environment overrides, resolve paths, and prepare runtime directories.
        """
        # Keep existing installations on their original database location.
        legacy_storage = BASE_DIR / "storage"
        default_storage = (
            legacy_storage if (legacy_storage / "agent.db").exists()
            else Path.home() / ".local/share/gemma-agents"
        )
        storage = Path(os.getenv("AGENT_STORAGE", default_storage))
        storage = storage.expanduser().resolve()
        workspace = Path(os.getenv("AGENT_WORKSPACE", Path.cwd() / "workspace"))
        settings = cls(
            model=os.getenv("AGENT_MODEL", "gemma4:12b-mlx"),
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            workspace=workspace.expanduser().resolve(),
            storage_dir=storage,
            database_path=storage / "agent.db",
            system_prompt_path=BASE_DIR / "prompts/system.md",
            max_agent_steps=int(os.getenv("AGENT_MAX_STEPS", "20")),
            command_timeout=int(os.getenv("AGENT_COMMAND_TIMEOUT", "120")),
            keep_alive=os.getenv("AGENT_KEEP_ALIVE", "15m"),
            embedding_model=os.getenv("AGENT_EMBEDDING_MODEL", "embeddinggemma"),
            sandbox=os.getenv("AGENT_SANDBOX", "required"),
            skills_dir=Path(os.getenv("AGENT_SKILLS", storage / "skills"))
            .expanduser().resolve(),
            searxng_url=os.getenv("AGENT_SEARXNG_URL", ""),
            api_token=os.getenv("AGENT_API_TOKEN", ""),
            context_chars=int(os.getenv("AGENT_CONTEXT_CHARS", "60000")),
            repair_attempts=int(os.getenv("AGENT_REPAIR_ATTEMPTS", "2")),
        )
        settings.prepare()
        return settings

    def prepare(self) -> None:
        """Validate limits and protected paths, then create workspace and storage.

        Raise ValueError if the workspace contains runtime code, storage, or
        trusted skills, or if sandbox and resource settings are invalid.
        """
        if self.sandbox not in {"required", "off"}:
            raise ValueError("AGENT_SANDBOX doit valoir required ou off.")
        if min(self.max_agent_steps, self.command_timeout) < 1:
            raise ValueError("Les limites doivent être positives.")
        if self.context_chars < 4000:
            raise ValueError("AGENT_CONTEXT_CHARS doit être au moins 4000.")
        if not 0 <= self.repair_attempts <= 5:
            raise ValueError("AGENT_REPAIR_ATTEMPTS doit être entre 0 et 5.")
        protected = [BASE_DIR, self.storage_dir, self.database_path]
        if self.skills_dir:
            protected.append(self.skills_dir)
        for path in protected:
            if path.resolve().is_relative_to(self.workspace.resolve()):
                raise ValueError(
                    "Le workspace doit être distinct du code du runtime, "
                    "du stockage et des skills de confiance."
                )
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

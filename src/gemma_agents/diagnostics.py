"""Read-only installation diagnostics, separate from presentation and agent work."""

import platform
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Diagnostic:
    """One observed prerequisite; optional failures do not prevent operation."""

    name: str
    ok: bool
    detail: str
    required: bool = True


def diagnose(runtime) -> dict:
    """Probe sandbox execution, executable discovery and Ollama tool capability.

    Never download a model or execute project code. The model check inspects
    metadata, not inference quality; real-model evaluation is a separate gate.
    """
    checks = [Diagnostic("platform", platform.system() == "Darwin"
                         and platform.machine() == "arm64", platform.platform())]
    checks.append(Diagnostic("isolation", runtime.settings.sandbox == "required",
                             runtime.settings.sandbox))
    for name in ("uv", "git"):
        try:
            checks.append(Diagnostic(name, True, runtime.runner.executable(name)))
        except (OSError, ValueError) as error:
            checks.append(Diagnostic(name, False, str(error)))
    try:
        result = runtime.runner.run(["pwd"], timeout=10)
        checks.append(Diagnostic("sandbox", result.startswith("EXIT CODE: 0\n"),
                                 result))
    except Exception as error:
        checks.append(Diagnostic("sandbox", False, str(error)))
    try:
        runtime.llm.validate_model(runtime.settings.model)
        checks.append(Diagnostic("model", True, runtime.settings.model))
    except Exception as error:
        checks.append(Diagnostic("model", False, str(error)))
    return {"ok": all(c.ok or not c.required for c in checks),
            "workspace": str(runtime.settings.workspace),
            "storage": str(runtime.settings.storage_dir),
            "checks": [asdict(c) for c in checks]}

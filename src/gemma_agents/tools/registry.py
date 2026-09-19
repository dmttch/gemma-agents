"""Register typed tool functions and strictly validate their call arguments."""

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import get_type_hints

from pydantic import ConfigDict, create_model


@dataclass(frozen=True)
class ToolSpec:
    """Immutable callable metadata with risk level and a generated argument schema."""

    name: str
    function: Callable
    risk: str
    description: str
    arguments_model: type


class ToolRegistry:
    """Build strict argument models from registered Python function signatures."""

    def __init__(self):
        """Initialize an empty tool registry keyed by function name."""
        self._tools = {}

    def register(self, function: Callable, *, risk: str = "low",
                 description: str = "") -> None:
        """Register a uniquely named typed function and generate its strict input model.
        """
        name = function.__name__
        if name in self._tools:
            raise ValueError(f"Outil déjà enregistré : {name}")
        hints = get_type_hints(function)
        fields = {}
        for parameter in inspect.signature(function).parameters.values():
            # Pydantic uses ellipsis for required fields; real Python defaults
            # must remain optional in the generated tool argument model.
            default = (... if parameter.default is inspect.Parameter.empty
                       else parameter.default)
            fields[parameter.name] = (hints[parameter.name], default)
        model = create_model(name + "Arguments",
                             __config__=ConfigDict(extra="forbid", strict=True),
                             **fields)
        self._tools[name] = ToolSpec(name, function, risk, description, model)

    def get(self, name: str) -> ToolSpec | None:
        """Return a tool specification, or None when the name is unknown."""
        return self._tools.get(name)

    def ollama_tools(self) -> list[Callable]:
        """Return registered callables for Ollama's tool-schema generation."""
        return [spec.function for spec in self._tools.values()]

    def validate(self, name: str, arguments: dict) -> dict:
        """Reject unknown tools or invalid inputs and return validated argument values.
        """
        spec = self.get(name)
        if spec is None:
            raise ValueError(f"Outil inconnu : {name}")
        return spec.arguments_model.model_validate(arguments).model_dump()

    def execute(self, name: str, arguments: dict):
        """Validate arguments and invoke a tool; callers must enforce policy separately.
        """
        return self._tools[name].function(**self.validate(name, arguments))

    def names(self) -> list[str]:
        """Return tool names in registration order."""
        return list(self._tools)

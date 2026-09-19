"""Explicit tool failures that preserve the existing textual tool interface."""


class ToolFailure(str):
    """A handled failure, readable by the model but never counted as execution success.

    Unlike exceptions, these failures preserve actionable domain wording. The
    gateway inspects the type before serialization; callers still receive text.
    """

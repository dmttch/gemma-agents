"""Format tool output and strip terminal control characters for display."""

import json


def safe_text(value: str) -> str:
    """Remove C0/C1 control characters while preserving tabs, newlines, and text."""
    return "".join(c for c in str(value) if c in "\n\t" or (
        ord(c) >= 32 and not 127 <= ord(c) <= 159))


def tool_content(name: str, content: str) -> str:
    """Render file-read JSON as an excerpt, falling back to sanitized raw output."""
    if name == "read_file":
        try:
            data = json.loads(content)
            return safe_text(f"{data['path']} · lignes {data['start_line']}–"
                             f"{data['end_line']}\n{data['content']}"
                             + ("\n[Extrait borné]" if data["truncated"] else ""))
        except (ValueError, KeyError, TypeError):
            pass
    return safe_text(content)

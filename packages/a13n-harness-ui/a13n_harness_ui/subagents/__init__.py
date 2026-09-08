"""Release-owned Markdown subagents; inclusion never copies files into user configuration."""

from importlib.resources import files
from typing import Literal

type BuiltinSubagentName = Literal["code-reviewer", "executor", "explorer"]

BUILTIN_SUBAGENT_NAMES: tuple[BuiltinSubagentName, ...] = ("code-reviewer", "executor", "explorer")


def builtin_subagent_sources() -> tuple[tuple[str, bytes], ...]:
    """Read the small fixed catalog from the installed package."""
    return tuple((name, files(__package__).joinpath(f"{name}.md").read_bytes()) for name in BUILTIN_SUBAGENT_NAMES)

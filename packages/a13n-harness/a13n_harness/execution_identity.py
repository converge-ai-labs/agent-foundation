"""Shared model-facing execution identity for retrieval and Host delivery."""

from typing import Literal


def format_execution_header(
    kind: Literal["Subagent notification", "Subagent result", "Subagent message", "Tool result", "Tool notification"],
    name: str,
    execution_id: str,
    state: str,
) -> str:
    """Label an observation with its original execution ID, never a delivery ID."""
    return f"{kind} · {name} · execution_id={execution_id} · {state}"


__all__ = ["format_execution_header"]

"""Stable safe errors for Agent Preset management."""

from __future__ import annotations


class AgentPresetError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def preset_not_found() -> AgentPresetError:
    return AgentPresetError("preset_not_found", "The AgentPreset was not found.", status_code=404)


def preset_revision_not_found() -> AgentPresetError:
    return AgentPresetError("preset_revision_not_found", "The AgentPresetRevision was not found.", status_code=404)


def resource_version_conflict(current_version: int) -> AgentPresetError:
    return AgentPresetError(
        "resource_version_conflict",
        "The AgentPreset resource version has changed.",
        status_code=409,
        details={"current_version": current_version},
    )


def preset_publish_failed(reason: str, *, path: str | None = None) -> AgentPresetError:
    details: dict[str, object] = {"reason": reason}
    if path is not None:
        details["path"] = path
    return AgentPresetError(
        "preset_publish_failed",
        "The AgentPreset configuration could not be published.",
        status_code=409,
        details=details,
    )

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


def preset_not_published() -> AgentPresetError:
    return AgentPresetError("preset_not_published", "The AgentPreset has no active Revision.", status_code=409)


def preset_disabled() -> AgentPresetError:
    return AgentPresetError("preset_disabled", "The AgentPreset is disabled.", status_code=409)


def preset_archived() -> AgentPresetError:
    return AgentPresetError("preset_archived", "The AgentPreset is archived.", status_code=409)


def preset_revision_not_executable(reason: str | None = None) -> AgentPresetError:
    details: dict[str, object] | None = {"reason": reason} if reason is not None else None
    return AgentPresetError(
        "preset_revision_not_executable",
        "The AgentPresetRevision cannot currently be executed.",
        status_code=409,
        details=details,
    )


def active_revision_conflict(current_revision_id: str | None) -> AgentPresetError:
    return AgentPresetError(
        "active_revision_conflict",
        "The active AgentPresetRevision has changed.",
        status_code=409,
        details={"current_revision_id": current_revision_id},
    )


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


def invalid_run_override(path: str, reason: str) -> AgentPresetError:
    """Return one bounded validation error for an invalid typed Run override."""

    return AgentPresetError(
        "validation_error",
        "The Agent Run configuration override is invalid.",
        status_code=422,
        details={"path": path, "reason": reason},
    )

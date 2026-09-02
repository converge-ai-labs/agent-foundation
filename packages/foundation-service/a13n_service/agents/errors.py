"""Stable safe errors for Agent Agent management."""

from __future__ import annotations


class AgentError(Exception):
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


def agent_not_found() -> AgentError:
    return AgentError("agent_not_found", "The Agent was not found.", status_code=404)


def agent_revision_not_found() -> AgentError:
    return AgentError("agent_revision_not_found", "The AgentRevision was not found.", status_code=404)


def agent_current_revision_missing() -> AgentError:
    return AgentError(
        "agent_current_revision_missing",
        "The Agent has no current Revision.",
        status_code=409,
    )


def agent_disabled() -> AgentError:
    return AgentError("agent_disabled", "The Agent is disabled.", status_code=409)


def agent_archived() -> AgentError:
    return AgentError("agent_archived", "The Agent is archived.", status_code=409)


def agent_revision_not_executable(reason: str | None = None) -> AgentError:
    details: dict[str, object] | None = {"reason": reason} if reason is not None else None
    return AgentError(
        "agent_revision_not_executable",
        "The AgentRevision cannot currently be executed.",
        status_code=409,
        details=details,
    )


def current_revision_conflict(current_revision_id: str | None) -> AgentError:
    return AgentError(
        "current_revision_conflict",
        "The default AgentRevision has changed.",
        status_code=409,
        details={"current_current_revision_id": current_revision_id},
    )


def agent_version_conflict(current_version: int) -> AgentError:
    return AgentError(
        "agent_version_conflict",
        "The Agent version has changed.",
        status_code=409,
        details={"current_version": current_version},
    )


def agent_revision_create_failed(reason: str, *, path: str | None = None) -> AgentError:
    details: dict[str, object] = {"reason": reason}
    if path is not None:
        details["path"] = path
    return AgentError(
        "agent_revision_create_failed",
        "The Agent configuration could not be resolved into a Revision.",
        status_code=409,
        details=details,
    )


def invalid_run_override(path: str, reason: str) -> AgentError:
    """Return one bounded validation error for an invalid typed Run override."""

    return AgentError(
        "validation_error",
        "The Agent Run configuration override is invalid.",
        status_code=422,
        details={"path": path, "reason": reason},
    )

"""Stable safe errors for Agent management."""

from __future__ import annotations

from a13n_service.iam import AuthorizationError
from a13n_service.models.service import ModelError
from a13n_service.public_errors import PublicError


class AgentError(PublicError):
    pass


def map_authorization_error(error: AuthorizationError, *, exact: bool = False) -> AgentError:
    if exact or error.concealed:
        return agent_not_found()
    return AgentError("forbidden", "The operation is not allowed.", status_code=403)


def builtin_identity_conflict() -> AgentError:
    return AgentError(
        "agent_state_conflict",
        "The built-in Agent identity is already in use.",
        status_code=409,
    )


def invalid_idempotency_key() -> AgentError:
    return AgentError(
        "invalid_request",
        "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
        status_code=400,
    )


def idempotency_conflict() -> AgentError:
    return AgentError(
        "idempotency_conflict",
        "The Idempotency-Key was already used with different request content.",
        status_code=409,
    )


def model_error_reason(error: ModelError) -> str:
    return {
        "model_not_found": "model_unavailable",
        "model_disabled": "model_unavailable",
        "credential_not_eligible": "model_credential_unavailable",
        "model_configuration_changed": "model_configuration_changed",
        "invalid_model_configuration": "model_incompatible",
    }.get(error.code, "model_unavailable")


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
        "The current AgentRevision has changed.",
        status_code=409,
        details={"current_revision_id": current_revision_id},
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

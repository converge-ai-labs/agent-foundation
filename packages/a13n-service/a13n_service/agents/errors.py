"""Stable safe errors for Agent management."""

from __future__ import annotations

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.iam import AuthorizationError
from a13n_service.models.service import ModelError


class AgentError(ApplicationError):
    pass


def map_authorization_error(error: AuthorizationError, *, exact: bool = False) -> AgentError:
    if exact or error.concealed:
        return agent_not_found()
    return AgentError("forbidden", "The operation is not allowed.", category=ErrorCategory.forbidden)


def builtin_identity_conflict() -> AgentError:
    return AgentError(
        "agent_state_conflict",
        "The built-in Agent identity is already in use.",
        category=ErrorCategory.conflict,
    )


def invalid_idempotency_key() -> AgentError:
    return AgentError(
        "invalid_request",
        "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
        category=ErrorCategory.invalid_request,
    )


def idempotency_conflict() -> AgentError:
    return AgentError(
        "idempotency_conflict",
        "The Idempotency-Key was already used with different request content.",
        category=ErrorCategory.conflict,
    )


def map_model_error(error: ModelError) -> AgentError:
    if error.code == "invalid_model_settings":
        return AgentError(error.code, error.message, category=error.category, details=error.details)
    return agent_revision_not_executable(model_error_reason(error))


def model_error_reason(error: ModelError) -> str:
    """Map Model setup failures to the stable Agent-facing reason vocabulary."""

    return {
        "model_not_found": "model_unavailable",
        "model_disabled": "model_unavailable",
        "credential_not_eligible": "model_credential_unavailable",
        "model_configuration_changed": "model_configuration_changed",
        "invalid_model_configuration": "model_incompatible",
    }.get(error.code, "model_unavailable")


def agent_not_found() -> AgentError:
    return AgentError("agent_not_found", "The Agent was not found.", category=ErrorCategory.not_found)


def agent_revision_not_found() -> AgentError:
    return AgentError("agent_revision_not_found", "The AgentRevision was not found.", category=ErrorCategory.not_found)


def agent_default_revision_missing() -> AgentError:
    return AgentError(
        "agent_default_revision_missing",
        "The Agent has no current Revision.",
        category=ErrorCategory.conflict,
    )


def agent_disabled() -> AgentError:
    return AgentError("agent_disabled", "The Agent is disabled.", category=ErrorCategory.conflict)


def agent_archived() -> AgentError:
    return AgentError("agent_archived", "The Agent is archived.", category=ErrorCategory.conflict)


def agent_revision_not_executable(reason: str | None = None) -> AgentError:
    details: dict[str, object] | None = {"reason": reason} if reason is not None else None
    return AgentError(
        "agent_revision_not_executable",
        "The AgentRevision cannot currently be executed.",
        category=ErrorCategory.conflict,
        details=details,
    )


def default_revision_conflict(default_revision_id: str | None) -> AgentError:
    return AgentError(
        "default_revision_conflict",
        "The current AgentRevision has changed.",
        category=ErrorCategory.conflict,
        details={"default_revision_id": default_revision_id},
    )


def agent_revision_create_failed(reason: str, *, path: str | None = None) -> AgentError:
    details: dict[str, object] = {"reason": reason}
    if path is not None:
        details["path"] = path
    return AgentError(
        "agent_revision_create_failed",
        "The Agent configuration could not be resolved into a Revision.",
        category=ErrorCategory.conflict,
        details=details,
    )


def invalid_run_override(path: str, reason: str) -> AgentError:
    """Return one bounded validation error for an invalid typed Run override."""

    return AgentError(
        "validation_error",
        "The Agent Run configuration override is invalid.",
        category=ErrorCategory.invalid_input,
        details={"path": path, "reason": reason},
    )

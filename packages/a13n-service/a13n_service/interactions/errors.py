"""Shared interaction application errors."""

from __future__ import annotations

from a13n_service.application_errors import ApplicationError, ErrorCategory


class RunAcceptanceError(ApplicationError):
    def __init__(self, code: str, message: str, *, category: ErrorCategory = ErrorCategory.conflict) -> None:
        super().__init__(code, message, category=category)


class InteractionCommandError(ApplicationError):
    """A bounded command failure safe for every Gateway adapter."""


def map_acceptance_error(error: RunAcceptanceError) -> InteractionCommandError:
    return InteractionCommandError(error.code, str(error), category=error.category)


def command_not_found() -> InteractionCommandError:
    return InteractionCommandError(
        "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
    )


def idempotency_conflict() -> InteractionCommandError:
    return InteractionCommandError(
        "idempotency_conflict",
        "The Idempotency-Key was already used with different request content.",
        category=ErrorCategory.conflict,
    )

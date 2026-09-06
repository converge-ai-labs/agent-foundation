"""Shared interaction application errors."""

from __future__ import annotations

from a13n_service.application_errors import ApplicationError, ErrorCategory


class RunAcceptanceError(ApplicationError):
    def __init__(self, code: str, message: str, *, category: ErrorCategory = ErrorCategory.conflict) -> None:
        super().__init__(code, message, category=category)

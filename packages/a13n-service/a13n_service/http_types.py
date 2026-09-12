"""Shared HTTP parameter declarations."""

from typing import Annotated

from fastapi import Depends, Header

from a13n_service.application_errors import ApplicationError, ErrorCategory

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]


def _if_match(value: Annotated[str | None, Header(alias="If-Match", min_length=1, max_length=256)] = None) -> str:
    if value is None:
        raise ApplicationError(
            "precondition_required", "If-Match is required.", category=ErrorCategory.precondition_required
        )
    return value


IfMatch = Annotated[str, Depends(_if_match)]

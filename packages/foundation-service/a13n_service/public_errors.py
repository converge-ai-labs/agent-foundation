"""Stable safe errors which may cross the Foundation HTTP boundary."""

from __future__ import annotations

from collections.abc import Mapping


class PublicError(Exception):
    """A bounded application failure safe to expose through the public API."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.headers = dict(headers or {})


__all__ = ["PublicError"]

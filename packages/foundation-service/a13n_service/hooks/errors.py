"""Hook management error contract."""

from __future__ import annotations

from a13n_service.public_errors import PublicError


class HookManagementError(PublicError):
    """A safe Hook management error exposed through the public API."""


__all__ = ["HookManagementError"]

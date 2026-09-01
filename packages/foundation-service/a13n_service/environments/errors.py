"""Safe Environment Management application errors."""

from __future__ import annotations


class EnvironmentManagementError(Exception):
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


def environment_not_found() -> EnvironmentManagementError:
    return EnvironmentManagementError("environment_not_found", "The Environment was not found.", status_code=404)


def environment_revision_not_found() -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "environment_revision_not_found",
        "The Environment Revision was not found.",
        status_code=404,
    )


def environment_provider_not_found() -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "environment_provider_not_found",
        "The Environment Provider was not found.",
        status_code=404,
    )


def environment_provider_disabled() -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "environment_provider_disabled",
        "The Environment Provider is not enabled in this Workspace.",
        status_code=409,
    )


def environment_version_conflict(current_version: int) -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "environment_version_conflict",
        "The Environment changed before this mutation was accepted.",
        status_code=409,
        details={"current_version": current_version},
    )


def environment_provider_version_conflict(current_version: int | None) -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "environment_provider_version_conflict",
        "The Workspace Environment Provider selection changed before this mutation was accepted.",
        status_code=409,
        details={"current_version": current_version},
    )

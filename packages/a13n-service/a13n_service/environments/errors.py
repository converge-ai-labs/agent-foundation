"""Safe Environment application errors."""

from a13n_service.application_errors import ApplicationError, ErrorCategory


class EnvironmentManagementError(ApplicationError):
    pass


def environment_not_found() -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "environment_not_found", "Environment resource was not found.", category=ErrorCategory.not_found
    )


def invalid_environment(message: str) -> EnvironmentManagementError:
    return EnvironmentManagementError("environment_invalid", message, category=ErrorCategory.invalid_input)


def provider_unavailable() -> EnvironmentManagementError:
    """A stored Provider type that this deployment no longer selects is a configuration fault."""
    return EnvironmentManagementError(
        "environment_provider_unavailable",
        "The Environment Provider implementation is not available in this deployment.",
        category=ErrorCategory.unavailable,
    )


def is_target_identity_conflict(error: BaseException) -> bool:
    from sqlalchemy.exc import IntegrityError

    if not isinstance(error, IntegrityError):
        return False
    message = str(error.orig).casefold()
    return (
        "uq_environments_provider_target" in message
        or "unique constraint failed: environments.target_identity" in message
    )


def connection_dependency_unavailable() -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "environment_coordination_unavailable",
        "Client Environment connection coordination is unavailable.",
        category=ErrorCategory.unavailable,
    )

"""Safe Environment application errors."""

from a13n_service.public_errors import PublicError


class EnvironmentManagementError(PublicError):
    pass


def environment_not_found() -> EnvironmentManagementError:
    return EnvironmentManagementError("environment_not_found", "Environment resource was not found.", status_code=404)


def invalid_environment(message: str) -> EnvironmentManagementError:
    return EnvironmentManagementError("environment_invalid", message, status_code=422)

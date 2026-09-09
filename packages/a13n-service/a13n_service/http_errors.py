"""HTTP projection of explicit application failures."""

from a13n_service.application_errors import ApplicationError, ErrorCategory

_STATUS = {
    ErrorCategory.invalid_request: 400,
    ErrorCategory.unauthenticated: 401,
    ErrorCategory.forbidden: 403,
    ErrorCategory.not_found: 404,
    ErrorCategory.not_acceptable: 406,
    ErrorCategory.conflict: 409,
    ErrorCategory.stale_version: 412,
    ErrorCategory.precondition_required: 428,
    ErrorCategory.size_limit: 413,
    ErrorCategory.unsupported_media: 415,
    ErrorCategory.invalid_input: 422,
    ErrorCategory.rate_limited: 429,
    ErrorCategory.internal: 500,
    ErrorCategory.dependency_failure: 502,
    ErrorCategory.unavailable: 503,
    ErrorCategory.timeout: 504,
}


def application_error_status(error: ApplicationError) -> int:
    return _STATUS[error.category]


def application_error_headers(error: ApplicationError) -> dict[str, str]:
    return {} if error.retry_after_seconds is None else {"Retry-After": str(error.retry_after_seconds)}

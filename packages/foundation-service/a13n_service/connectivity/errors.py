from a13n_service.application_errors import ApplicationError


class NativeError(ApplicationError):
    """Safe stable error from native Account and Ingress operations."""

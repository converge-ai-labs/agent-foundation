"""Host-supplied request authentication boundary."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request

from a13n_service.application_errors import ApplicationError
from a13n_service.request_runtime import get_process_runtime

from .authorization import AuthenticatedActor


class AuthenticationError(Exception):
    pass


class RequestAuthenticator(Protocol):
    async def __call__(self, request: Request) -> AuthenticatedActor: ...


async def authenticate_request(request: Request) -> AuthenticatedActor:
    runtime = get_process_runtime(request)
    authenticator = runtime.request_authenticator if runtime is not None else None
    if authenticator is None:
        raise AuthenticationError("authentication is not configured")
    try:
        actor = await authenticator(request)
    except AuthenticationError:
        raise
    except ApplicationError:
        raise
    except Exception as error:
        raise AuthenticationError("authentication failed") from error
    request.state.actor = actor
    return actor


async def authenticate_mutation(request: Request) -> AuthenticatedActor:
    """Require browser proof even for protocol callbacks historically exposed as GET."""
    from .http_auth import require_csrf

    actor = await authenticate_request(request)
    if actor.credential_source == "service" and actor.auth_method == "session":
        runtime = get_process_runtime(request)
        assert runtime is not None
        require_csrf(request, runtime.settings.identity_configuration())
    return actor

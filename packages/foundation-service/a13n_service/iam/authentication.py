"""Host-supplied request authentication boundary."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request

from .authorization import AuthenticatedActor


class AuthenticationError(Exception):
    pass


class RequestAuthenticator(Protocol):
    async def __call__(self, request: Request) -> AuthenticatedActor: ...


async def authenticate_request(request: Request) -> AuthenticatedActor:
    authenticator: RequestAuthenticator | None = getattr(request.app.state, "request_authenticator", None)
    if authenticator is None:
        raise AuthenticationError("authentication is not configured")
    try:
        actor = await authenticator(request)
    except AuthenticationError:
        raise
    except Exception as error:
        raise AuthenticationError("authentication failed") from error
    request.state.actor = actor
    return actor

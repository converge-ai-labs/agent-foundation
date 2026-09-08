"""Distribution-supplied request authentication port and its safe failure type."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request

from .domain import AuthenticatedActor


class AuthenticationError(Exception):
    pass


class RequestAuthenticator(Protocol):
    async def __call__(self, request: Request) -> AuthenticatedActor: ...

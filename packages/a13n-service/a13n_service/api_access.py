"""HTTP operation admission and audit context from the request's authenticated credential."""

from collections.abc import Iterator
from contextlib import contextmanager

from a13n_logging import log_context
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from a13n_service.infra.audit import operation_origin
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import DEPENDENCY_ERRORS, dependency_error_response, error_response, request_id
from a13n_service.tenancy.access import Authenticated
from a13n_service.tenancy.requests import current_credential, current_runtime


@contextmanager
def credential_context(credential: Authenticated, scope: Scope) -> Iterator[None]:
    """Keep execution origin through HTTP handling and MCP forwarding without inspecting token formats."""
    if credential.platform is None:
        yield
        return
    origin = credential.platform
    with (
        log_context(run_id=origin.run_id, attempt_id=origin.attempt_id),
        operation_origin(origin.run_id, origin.attempt_id, request_id(scope)),
    ):
        yield


class CredentialAccess:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        # MCP authenticates at its own mount after removing browser cookies. Login sessions continue to
        # authenticate through route dependencies, including their Origin/CSRF checks.
        if "authorization" not in request.headers or scope["path"] in {
            "/api/v1/mcp",
            "/api/v1/mcp/",
            "/api/v1/platform-tools",
            "/api/v1/platform-tools/",
        }:
            await self.app(scope, receive, send)
            return
        try:
            credential = await current_credential(request, await current_runtime(request))
            if credential.platform is not None:
                raise ServiceError("forbidden", "Run credentials only admit the internal business tool endpoint")
        except ServiceError as error:
            await error_response(error, request_id(scope))(scope, receive, send)
            return
        except tuple(DEPENDENCY_ERRORS) as error:
            response = await dependency_error_response(request, error)
            await response(scope, receive, send)
            return
        with credential_context(credential, scope):
            await self.app(scope, receive, send)

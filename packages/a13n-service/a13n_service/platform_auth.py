"""Application boundary for run credentials, shared by direct HTTP and MCP forwarding."""

from collections.abc import Sequence

from a13n_logging import log_context
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from a13n_service.infra.audit import operation_origin
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import DEPENDENCY_ERRORS, dependency_error_response, error_response, request_id
from a13n_service.infra.platform import PLATFORM_METADATA, RUN_CREDENTIAL, RUN_GROUPS, PlatformOperation
from a13n_service.runs.platform_tokens import PREFIX, authenticate


class RunTokenBoundary:
    def __init__(self, app: ASGIApp, routes: Sequence[APIRoute]):
        self.app = app
        self.routes = routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.startswith(PREFIX):
            await self.app(scope, receive, send)
            return
        try:
            credential, claims = await authenticate(request.app.state.runtime, token)
            if scope["path"] not in {"/api/v1/mcp", "/api/v1/mcp/"}:
                route = next((route for route in self.routes if route.matches(scope)[0] == Match.FULL), None)
                extra = route.openapi_extra or {} if route else {}
                metadata = extra.get(PLATFORM_METADATA) if extra.get("x-a13n-mcp") is True else None
                if metadata is None or PlatformOperation.model_validate(metadata).group not in claims.groups:
                    raise ServiceError("forbidden", "Run credential does not admit this operation")
            state = scope.setdefault("state", {})
            state[RUN_CREDENTIAL] = credential
            state[RUN_GROUPS] = claims.groups
        except ServiceError as error:
            await error_response(error, request_id(scope))(scope, receive, send)
            return
        except tuple(DEPENDENCY_ERRORS) as error:
            response = await dependency_error_response(request, error)
            await response(scope, receive, send)
            return
        with (
            log_context(run_id=claims.run_id, attempt_id=claims.attempt_id),
            operation_origin(claims.run_id, claims.attempt_id, request_id(scope)),
        ):
            await self.app(scope, receive, send)

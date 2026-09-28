"""Request IDs and the request log, the HTTP error envelope, strong resource preconditions, request idempotency keys
and the headers stored content is served with."""

import time
from typing import Annotated, Any, Protocol
from urllib.parse import quote

from a13n_logging import exception_details, get_logger, log_context
from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, JsonValue
from redis.exceptions import RedisError
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from a13n_service.infra.errors import RETRY_AFTER, ErrorCode, ServiceError, invalid, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.telemetry import meter

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-Id"

REQUEST_DURATION = meter.create_histogram(
    "http.server.request.duration",
    unit="s",
    description="Time from receiving an HTTP request until its response ends, streams included",
    explicit_bucket_boundaries_advisory=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10),
)
# Probes answer every few seconds and say nothing about a request; they are measured but not logged.
_UNLOGGED_ROUTES = frozenset({"/healthz", "/readyz"})

_STATUS: dict[ErrorCode, int] = {
    "invalid_argument": 400,
    "invalid_cursor": 400,
    "unauthenticated": 401,
    "forbidden": 403,
    "not_found": 404,
    "already_exists": 409,
    "conflict": 409,
    "precondition_failed": 412,
    "precondition_required": 428,
    "payload_too_large": 413,
    "request_timeout": 408,
    "disabled": 422,
    "unavailable": 503,
    "rate_limited": 429,
    "internal": 500,
}


class RequestIds:
    """Give every HTTP request an ID, returned as `X-Request-Id` and in error bodies to match reports to logs, and
    add the headers its handling asked every answer to carry (`answer_headers`).

    Every record logged while the request is handled carries its ID, and its end is logged and measured by route
    template, never by URL: paths and query strings can carry identifiers and authorization codes.

    The ID grants nothing and is never taken from the caller. A defect's response is sent outside this middleware
    (Starlette's `ServerErrorMiddleware`), so `error_response` sets the ID too; either way it appears once, and
    a request whose response never started here ends as that 500.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = new_object_id("req")
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        started, status = time.perf_counter(), 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                answer: Response | None = state.get(_ANSWER)
                if answer is not None:
                    own = {name for name, _ in headers.raw}
                    # Ahead of the response's own headers, which win: one it sets is kept, and a cookie it sets
                    # comes after one of the same name set here (cookies are never combined, so each is added).
                    headers.raw[:0] = [
                        (name, value) for name, value in answer.headers.raw if name == b"set-cookie" or name not in own
                    ]
            await send(message)

        with log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                _finished(scope, status, time.perf_counter() - started)


def _finished(scope: Scope, status: int, seconds: float) -> None:
    route = scope.get("route")
    template = route.path if route is not None else "unmatched"
    REQUEST_DURATION.record(
        seconds,
        {"http.request.method": scope["method"], "http.route": template, "http.response.status_code": status},
    )
    if template not in _UNLOGGED_ROUTES:
        logger.info(
            "Request finished",
            extra={
                "method": scope["method"],
                "route": template,
                "status": status,
                "duration_ms": round(seconds * 1000),
            },
        )


def request_id(scope: Scope) -> str | None:
    return scope.get("state", {}).get("request_id")


_ANSWER = "answer_headers"


def answer_headers(request: Request) -> Response:
    """Headers every response to `request` carries, whatever answers it: a serialized result, the route's own
    `Response` or an error (FastAPI merges an injected `Response` into the first only)."""
    state = request.scope.setdefault("state", {})
    answer = state.get(_ANSWER)
    if answer is None:
        answer = state[_ANSWER] = Response()
        del answer.headers["content-length"]
    return answer


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, JsonValue]
    # The response's `X-Request-Id`, to match a report to the Service's logs.
    request_id: str | None


class ErrorEnvelope(BaseModel):
    """How every failure answers, whatever its status."""

    error: ErrorBody


def error_response(error: ServiceError, request_id: str | None) -> JSONResponse:
    headers = {"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}
    if request_id is not None:
        headers[REQUEST_ID_HEADER] = request_id
    if error.code == "rate_limited":
        headers["Retry-After"] = str(error.details.get(RETRY_AFTER, 1))
    if error.code in {"payload_too_large", "request_timeout"}:
        headers["Connection"] = "close"  # the rest of the body is never read
    body = {"code": error.code, "message": error.message, "details": error.details, "request_id": request_id}
    return JSONResponse({"error": body}, status_code=_STATUS[error.code], headers=headers)


async def service_error_response(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, ServiceError):
        raise error
    return error_response(error, request_id(request.scope))


async def validation_error_response(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise error
    # Pydantic input and custom messages can contain secrets; expose only locations and codes.
    fields: list[JsonValue] = [
        {"field": ".".join(str(part) for part in item["loc"]), "reason": str(item["type"])}
        for item in error.errors()[:20]
    ]
    failure = ServiceError("invalid_argument", "Request validation failed", {"fields": fields})
    return error_response(failure, request_id(request.scope))


async def routing_error_response(request: Request, error: Exception) -> JSONResponse:
    """Refusals raised before a route runs: no route answers the method and path (404 and 405 alike), or the body
    could not be parsed (400)."""
    if not isinstance(error, HTTPException):
        raise error
    if error.status_code == 400:
        failure = invalid("body", "unparsable")
    else:
        failure = not_found("route", f"{request.method} {request.url.path}")
    return error_response(failure, request_id(request.scope))


# Failures that mean a dependency is unreachable or too slow, not that the request or the code is wrong.
DEPENDENCY_ERRORS: dict[type[Exception], str] = {
    OperationalError: "database",
    InterfaceError: "database",
    PoolTimeout: "database",
    RedisError: "redis",
}


async def dependency_error_response(request: Request, error: Exception) -> JSONResponse:
    dependency = next(name for kind, name in DEPENDENCY_ERRORS.items() if isinstance(error, kind))
    logger.warning(
        "Dependency unavailable",
        extra={
            "dependency": dependency,
            "error_type": type(error).__name__,
            "exception_details": exception_details(error),
        },
    )
    failure = ServiceError("unavailable", f"The {dependency} is unavailable", {"dependency": dependency})
    return error_response(failure, request_id(request.scope))


async def internal_error_response(request: Request, error: Exception) -> JSONResponse:
    """The envelope for a defect, logged with safe exception details and the request's ID. It answers outside
    `RequestIds`, whose log context has ended, so the ID is passed explicitly."""
    identity = request_id(request.scope)
    logger.error(
        "Unhandled error",
        extra={
            "error_type": type(error).__name__,
            "exception_details": exception_details(error),
            "request_id": identity,
        },
    )
    return error_response(ServiceError("internal", "Internal error"), identity)


def install_error_envelope(app: FastAPI) -> None:
    """Every failure a route lets escape answers in the one error envelope, with the request's ID.

    Install after other middleware, so the request ID wraps them all.
    """
    app.add_middleware(RequestIds)
    app.add_exception_handler(ServiceError, service_error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)
    app.add_exception_handler(HTTPException, routing_error_response)
    for kind in DEPENDENCY_ERRORS:
        app.add_exception_handler(kind, dependency_error_response)
    app.add_exception_handler(Exception, internal_error_response)


def document_errors(openapi: dict[str, Any]) -> None:
    """Describe failures in an OpenAPI document as the Service sends them: every API operation answers a failure
    in `ErrorEnvelope`, and request validation is `400 invalid_argument`, never FastAPI's default 422."""
    components = openapi.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    for unused in ("HTTPValidationError", "ValidationError"):
        schemas.pop(unused, None)
    envelope = ErrorEnvelope.model_json_schema(ref_template="#/components/schemas/{model}")
    schemas.update(envelope.pop("$defs"))
    schemas[ErrorEnvelope.__name__] = envelope
    components.setdefault("responses", {})["Error"] = {
        "description": "The failure, in the one error envelope",
        "headers": {REQUEST_ID_HEADER: {"schema": {"type": "string"}}},
        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{ErrorEnvelope.__name__}"}}},
    }
    error = {"$ref": "#/components/responses/Error"}
    for path, operations in openapi["paths"].items():
        if not path.startswith("/api/"):
            continue
        for operation in operations.values():
            responses = operation["responses"]
            if responses.pop("422", None) is not None:
                responses["400"] = error
            responses["default"] = error


class Versioned(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def version(self) -> int: ...


class KeyVersioned(Protocol):
    """A view of a kind identified by key: a model."""

    @property
    def key(self) -> str: ...

    @property
    def version(self) -> int: ...


def etag(identifier: str, version: int) -> str:
    return f'"{identifier}:{version}"'


def tagged[V: Versioned](response: Response, resource: V) -> V:
    """The resource, with its strong ETag set on the response."""
    response.headers["ETag"] = etag(resource.id, resource.version)
    return resource


def key_tagged[V: KeyVersioned](response: Response, resource: V) -> V:
    """`tagged` for a kind identified by key."""
    response.headers["ETag"] = etag(resource.key, resource.version)
    return resource


def require_match(value: str | None, identifier: str, version: int) -> None:
    """`identifier` is the resource's ID, or its key for a kind identified by key."""
    current = etag(identifier, version)
    if value is None:
        raise ServiceError("precondition_required", "If-Match is required", {"header": "If-Match"})
    if value != current:
        raise ServiceError("precondition_failed", "Resource changed", {"current_etag": current})


# Stored tenant bytes are never interpreted in the Service's origin: no type sniffing, no active content, no caching.
STORED_CONTENT_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, no-store",
}


def download_headers(filename: str | None) -> dict[str, str]:
    """Headers for stored bytes served as a file to save, never to render."""
    disposition = "attachment" if filename is None else f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    return {**STORED_CONTENT_HEADERS, "Content-Disposition": disposition}


# The ETag a client read, presented to change that resource; `require_match` decides whether it is current. The
# tag is the view's `"{id}:{version}"`, or `"{key}:{version}"` for a kind identified by key, so a list row gives
# it without another read.
IfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        max_length=512,
        description='The resource\'s ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model',
    ),
]

# The page size of a cursor-paged list.
PageLimit = Annotated[int, Query(ge=1, le=100)]

# Commands that create work take a caller-chosen key; a retry with the same key and body replays the result.
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512, pattern=r"^[!-~]+$")]

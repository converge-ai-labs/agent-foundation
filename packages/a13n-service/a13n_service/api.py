"""Shared Service HTTP request and error conventions."""

from __future__ import annotations

import secrets
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from a13n_service.application_errors import ApplicationError
from a13n_service.http_errors import application_error_headers, application_error_status
from a13n_service.iam import AuthenticationError, AuthorizationError


def install_api_conventions(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID")
        request_id = supplied if supplied and _safe_request_id(supplied) else f"req-{secrets.token_hex(12)}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        # Framework routing/parser failures use the same public envelope. Detail
        # can contain arbitrary exception data, so only code-owned text is exposed.
        code, message = {
            400: ("invalid_request", "The request is invalid."),
            401: ("authentication_required", "Authentication is required."),
            403: ("permission_denied", "Permission denied."),
            404: ("resource_not_found", "The requested resource was not found."),
            405: ("method_not_allowed", "The request method is not allowed for this resource."),
            409: ("conflict", "The request conflicts with current state."),
            413: ("request_too_large", "The request exceeds the allowed size."),
            422: ("invalid_request", "The request is invalid."),
            429: ("rate_limited", "The request rate or capacity limit was reached."),
            500: ("internal_error", "An unexpected service error occurred."),
            503: ("service_unavailable", "The service is temporarily unavailable."),
        }.get(error.status_code, ("http_error", "The HTTP request could not be completed."))
        return api_error_response(request, error.status_code, code, message, headers=error.headers)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _error: Exception) -> JSONResponse:
        # Starlette retains exception propagation/logging and does not replace a
        # response that has already started (for example, an interrupted stream).
        return api_error_response(request, 500, "internal_error", "An unexpected service error occurred.")

    @app.exception_handler(AuthenticationError)
    async def authentication_error(request: Request, _error: AuthenticationError) -> JSONResponse:
        return api_error_response(request, 401, "authentication_required", "Authentication is required.")

    @app.exception_handler(ApplicationError)
    async def public_error_handler(request: Request, error: ApplicationError) -> JSONResponse:
        return api_error_response(
            request,
            application_error_status(error),
            error.code,
            error.message,
            error.details,
            headers=application_error_headers(error),
        )

    @app.exception_handler(AuthorizationError)
    async def authorization_error(request: Request, error: AuthorizationError) -> JSONResponse:
        return api_error_response(
            request,
            404 if error.concealed else 403,
            "resource_not_found" if error.concealed else "permission_denied",
            "The requested resource was not found." if error.concealed else "Permission denied.",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        fields = sorted(
            {
                ".".join(str(part) for part in item["loc"] if part not in {"body", "path", "query", "header"})[:128]
                for item in error.errors()
            }
        )
        return api_error_response(
            request,
            400,
            "invalid_request",
            "The request is invalid.",
            {"fields": [field for field in fields if field][:32]},
        )


def api_error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = request.scope.get("state", {}).get("request_id", "unknown")
    return JSONResponse(
        status_code=status_code,
        headers={**(headers or {}), "X-Request-ID": request_id},
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
            }
        },
    )


def _safe_request_id(value: str) -> bool:
    return len(value) <= 128 and all(0x21 <= ord(character) <= 0x7E for character in value)

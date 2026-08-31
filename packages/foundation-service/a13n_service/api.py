"""Shared Foundation HTTP request and error conventions."""

from __future__ import annotations

import secrets

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from a13n_service.iam import AuthenticationError
from a13n_service.model_management.service import ModelManagementError
from a13n_service.skill_management.errors import SkillManagementError


def install_api_conventions(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID")
        request_id = supplied if supplied and _safe_request_id(supplied) else f"req-{secrets.token_hex(12)}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AuthenticationError)
    async def authentication_error(request: Request, _error: AuthenticationError) -> JSONResponse:
        return _error_response(request, 401, "authentication_required", "Authentication is required.")

    @app.exception_handler(ModelManagementError)
    async def model_management_error(request: Request, error: ModelManagementError) -> JSONResponse:
        return _error_response(request, error.status_code, error.code, error.message, error.details)

    @app.exception_handler(SkillManagementError)
    async def skill_management_error(request: Request, error: SkillManagementError) -> JSONResponse:
        response = _error_response(request, error.status_code, error.code, error.message, error.details)
        if error.retry_after_seconds is not None:
            response.headers["Retry-After"] = str(error.retry_after_seconds)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        fields = sorted(
            {
                ".".join(str(part) for part in item["loc"] if part not in {"body", "path", "query", "header"})
                for item in error.errors()
            }
        )
        return _error_response(
            request,
            400,
            "invalid_request",
            "The request is invalid.",
            {"fields": [field for field in fields if field][:32]},
        )


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": getattr(request.state, "request_id", "unknown"),
            }
        },
    )


def _safe_request_id(value: str) -> bool:
    return len(value) <= 128 and all(0x21 <= ord(character) <= 0x7E for character in value)

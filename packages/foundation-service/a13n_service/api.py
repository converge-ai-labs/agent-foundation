"""Shared Foundation HTTP request and error conventions."""

from __future__ import annotations

import secrets

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from a13n_service.assets.errors import AssetError
from a13n_service.connectors import ConnectorError
from a13n_service.iam import AuthenticationError
from a13n_service.model_configs.service import ModelConfigError
from a13n_service.skills.errors import SkillError


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

    @app.exception_handler(AssetError)
    async def asset_error_handler(request: Request, error: AssetError) -> JSONResponse:
        return _error_response(request, error.status_code, error.code, error.message, error.details)

    @app.exception_handler(ModelConfigError)
    async def model_config_error_handler(request: Request, error: ModelConfigError) -> JSONResponse:
        return _error_response(request, error.status_code, error.code, error.message, error.details)

    @app.exception_handler(ConnectorError)
    async def connector_error(request: Request, error: ConnectorError) -> JSONResponse:
        status_code = _connector_status(error.code)
        return _error_response(request, status_code, error.code, str(error), dict(error.details))

    @app.exception_handler(SkillError)
    async def skill_error_handler(request: Request, error: SkillError) -> JSONResponse:
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


def _connector_status(code: str) -> int:
    if code in {"not_found", "provider_unavailable"}:
        return 404
    if code in {"permission_denied"}:
        return 403
    if code in {
        "version_conflict",
        "connector_in_use",
        "connection_incompatible",
        "trigger_in_use",
        "trigger_not_disabled",
    }:
        return 409
    if code in {"dependency_unavailable", "provider_load_failed"}:
        return 503
    return 400

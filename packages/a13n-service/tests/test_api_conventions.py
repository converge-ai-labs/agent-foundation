from __future__ import annotations

import httpx2
import pytest
from a13n_service.agents.errors import AgentError
from a13n_service.api import install_api_conventions
from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.skills.errors import SkillError
from fastapi import FastAPI
from pydantic import BaseModel


class _Payload(BaseModel):
    count: int


def _application() -> FastAPI:
    app = FastAPI()
    install_api_conventions(app)

    @app.get("/details")
    async def details() -> None:
        raise AgentError("example_conflict", "The example conflicts.", category=ErrorCategory.conflict, details={})

    @app.get("/retry")
    async def retry() -> None:
        raise SkillError(
            "github_rate_limited",
            "GitHub rate limit exceeded.",
            category=ErrorCategory.rate_limited,
            retry_after_seconds=12,
        )

    @app.post("/validated")
    async def validated(payload: _Payload) -> _Payload:
        return payload

    return app


@pytest.mark.anyio
async def test_public_error_handler_preserves_the_safe_error_snapshot() -> None:
    app = _application()
    transport = httpx2.ASGITransport(app=app)

    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/details", headers={"X-Request-ID": "req-public-error"})

    assert response.status_code == 409
    request_id = response.headers["X-Request-ID"]
    assert request_id.startswith("req-") and request_id != "req-public-error"
    assert response.json() == {
        "error": {
            "code": "example_conflict",
            "message": "The example conflicts.",
            "details": {},
            "request_id": request_id,
        }
    }
    assert ApplicationError in app.exception_handlers
    assert AgentError not in app.exception_handlers


@pytest.mark.anyio
async def test_skill_retry_after_is_carried_by_the_public_error() -> None:
    transport = httpx2.ASGITransport(app=_application())

    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/retry", headers={"X-Request-ID": "req-skill-retry"})

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "12"
    request_id = response.headers["X-Request-ID"]
    assert response.json() == {
        "error": {
            "code": "github_rate_limited",
            "message": "GitHub rate limit exceeded.",
            "details": {},
            "request_id": request_id,
        }
    }


@pytest.mark.anyio
async def test_request_validation_keeps_its_independent_error_handler() -> None:
    transport = httpx2.ASGITransport(app=_application())

    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/validated",
            json={"count": "not-an-integer"},
            headers={"X-Request-ID": "req-validation"},
        )

    assert response.status_code == 400
    request_id = response.headers["X-Request-ID"]
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "The request is invalid.",
            "details": {"fields": ["count"]},
            "request_id": request_id,
        }
    }


@pytest.mark.parametrize(
    "category,status",
    [
        (ErrorCategory.not_found, 404),
        (ErrorCategory.forbidden, 403),
        (ErrorCategory.invalid_request, 400),
        (ErrorCategory.stale_version, 412),
        (ErrorCategory.conflict, 409),
        (ErrorCategory.unavailable, 503),
    ],
)
def test_application_error_mapping_is_independent_of_code_and_wording(category, status) -> None:
    from a13n_service.application_errors import ApplicationError
    from a13n_service.http_errors import application_error_status

    for code, message in [
        ("opaque", "A changed internal explanation"),
        ("misleading_not_found", "idempotency_conflict"),
    ]:
        assert application_error_status(ApplicationError(code, message, category=category)) == status


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,code",
    [
        (400, "invalid_request"),
        (403, "permission_denied"),
        (404, "resource_not_found"),
        (405, "method_not_allowed"),
        (429, "rate_limited"),
        (503, "service_unavailable"),
    ],
)
async def test_framework_http_errors_use_safe_envelope_and_preserve_headers(status, code):
    from fastapi import HTTPException

    app = _application()

    @app.get("/framework-error")
    async def framework_error():
        raise HTTPException(status, detail={"secret": "private-provider-error"}, headers={"Retry-After": "12"})

    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/framework-error", headers={"X-Request-ID": "req-framework"})
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"] and error["details"] == {}
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert error["request_id"] != "req-framework"
    assert response.headers["Retry-After"] == "12"
    assert "private-provider-error" not in response.text


@pytest.mark.anyio
async def test_router_404_and_405_share_public_error_envelope():
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=_application()), base_url="http://testserver"
    ) as client:
        missing = await client.get("/missing")
        wrong_method = await client.get("/validated")
    for response, code in [(missing, "resource_not_found"), (wrong_method, "method_not_allowed")]:
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
        assert response.headers["X-Request-ID"].startswith("req-")
    assert wrong_method.headers["Allow"] == "POST"


@pytest.mark.anyio
async def test_unexpected_500_is_json_and_keeps_request_identity_without_exception_text():
    app = _application()

    @app.get("/broken")
    async def broken():
        raise RuntimeError("private-password SQL /private/host/path")

    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/broken", headers={"X-Request-ID": "req-unexpected"})
    assert response.status_code == 500
    request_id = response.headers["X-Request-ID"]
    assert request_id != "req-unexpected"
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected service error occurred.",
            "details": {},
            "request_id": request_id,
        }
    }

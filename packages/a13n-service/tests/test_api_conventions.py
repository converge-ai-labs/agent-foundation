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
    assert response.json() == {
        "error": {
            "code": "example_conflict",
            "message": "The example conflicts.",
            "details": {},
            "request_id": "req-public-error",
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
    assert response.json() == {
        "error": {
            "code": "github_rate_limited",
            "message": "GitHub rate limit exceeded.",
            "details": {},
            "request_id": "req-skill-retry",
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
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "The request is invalid.",
            "details": {"fields": ["count"]},
            "request_id": "req-validation",
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

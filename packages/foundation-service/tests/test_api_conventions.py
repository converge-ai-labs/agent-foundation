from __future__ import annotations

import httpx2
import pytest
from a13n_service.agents.errors import AgentError
from a13n_service.api import install_api_conventions
from a13n_service.public_errors import PublicError
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
        raise AgentError("example_conflict", "The example conflicts.", status_code=409, details={})

    @app.get("/retry")
    async def retry() -> None:
        raise SkillError(
            "github_rate_limited",
            "GitHub rate limit exceeded.",
            status_code=429,
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
    assert PublicError in app.exception_handlers
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

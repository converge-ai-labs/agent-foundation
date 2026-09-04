from __future__ import annotations

from datetime import UTC, datetime

import httpx2
import pytest
from a13n_service.api import install_api_conventions
from a13n_service.iam import AuthenticatedActor, PrincipalRef, PrincipalType
from a13n_service.trace_query import TraceCollection, TraceDetail, TraceQueryError, TraceQueryService, TraceView
from a13n_service.trace_query.router import router
from fastapi import FastAPI, Request


async def authenticate(_request: Request) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(
            principal_type=PrincipalType.user,
            principal_id="user_0000000000000001",
        ),
        auth_method="test",
        credential_id="credential-test",
        boundary_workspace_id="ws-1",
    )


class StubTraceQueryService:
    def __init__(self) -> None:
        self.list_arguments: dict[str, object] | None = None
        self.get_arguments: dict[str, object] | None = None

    async def list(self, **arguments: object) -> TraceCollection:
        self.list_arguments = arguments
        return TraceCollection(items=(), next_cursor=None)

    async def get(self, **arguments: object) -> TraceDetail:
        self.get_arguments = arguments
        raise TraceQueryError("trace_not_found", "The Trace was not found.", status_code=404)


def application(service: object, service_runtime_factory) -> FastAPI:
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router)
    app.state.runtime = service_runtime_factory(
        request_authenticator=authenticate,
        trace_queries=service,
    )
    return app


@pytest.mark.anyio
async def test_list_route_parses_the_native_contract(service_runtime_factory) -> None:
    service = StubTraceQueryService()
    transport = httpx2.ASGITransport(app=application(service, service_runtime_factory))

    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/workspaces/ws-1/traces",
            params={
                "from": "2026-09-01T00:00:00Z",
                "to": "2026-09-02T00:00:00Z",
                "query": "hello",
                "search_in": "input",
                "thread_id": "thread-1",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
    assert service.list_arguments is not None
    assert service.list_arguments["from_started_at"] == datetime(2026, 9, 1, tzinfo=UTC)
    assert service.list_arguments["to_started_at"] == datetime(2026, 9, 2, tzinfo=UTC)
    assert service.list_arguments["thread_id"] == "thread-1"


@pytest.mark.anyio
async def test_disabled_query_uses_the_shared_safe_error_envelope(service_runtime_factory) -> None:
    service = TraceQueryService(provider_key="none", provider=None, authorizer=None)
    transport = httpx2.ASGITransport(app=application(service, service_runtime_factory))

    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/workspaces/ws-1/traces",
            headers={"X-Request-ID": "req-trace-test"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "trace_query_unavailable",
            "message": "Trace Query is unavailable.",
            "details": {},
            "request_id": "req-trace-test",
        }
    }


@pytest.mark.anyio
async def test_route_validation_uses_400_and_detail_defaults_to_full(service_runtime_factory) -> None:
    service = StubTraceQueryService()
    app = application(service, service_runtime_factory)
    transport = httpx2.ASGITransport(app=app)

    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        invalid = await client.get("/api/v1/workspaces/ws-1/traces", params={"from": "not-a-time"})
        detail = await client.get("/api/v1/workspaces/ws-1/traces/trace-1")

    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert detail.status_code == 404
    assert service.get_arguments is not None
    assert service.get_arguments["view"] is TraceView.full

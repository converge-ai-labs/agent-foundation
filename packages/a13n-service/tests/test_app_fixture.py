"""Test-only contract reuse preserves production assembly and per-application isolation."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx2
import pytest
from a13n_service import api_tools as api_tools_module
from a13n_service import app as app_module
from a13n_service import mcp as mcp_module
from a13n_service.app import build_app
from a13n_service.distribution import OSS, Distribution
from a13n_service.mcp import build_mcp
from a13n_service.settings import Settings
from a13n_service.tenancy.authenticate import LocalAuthenticator
from fastapi import APIRouter, Depends, FastAPI
from fastmcp.tools import Tool
from fastmcp.utilities.openapi import HTTPRoute
from starlette.routing import Match

from .conftest import _include_individual_routes, _MatchedRouter


def test_service_apps_copy_the_contract_without_recompiling_it(
    service_app: Callable[..., FastAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings()
    created_tools: list[list[Tool]] = []
    api_tools = mcp_module.api_tools

    def capture(*args: Any, **kwargs: Any) -> list[Tool]:
        tools = api_tools(*args, **kwargs)
        created_tools.append(tools)
        return tools

    monkeypatch.setattr(mcp_module, "api_tools", capture)
    expected = build_app(role="control", settings=settings).openapi()
    expected_tools = [tool.model_dump() for tool in created_tools[-1]]
    original = FastAPI.openapi
    parse = api_tools_module.parse_openapi_to_http_routes
    heads = app_module.heads
    generated = parsed = declared = 0

    def counted(app: FastAPI) -> dict[str, Any]:
        nonlocal generated
        generated += 1
        return original(app)

    def counted_routes(document: dict[str, Any]) -> list[HTTPRoute]:
        nonlocal parsed
        parsed += 1
        return parse(document)

    def counted_heads(distribution: Distribution) -> tuple[str, ...]:
        nonlocal declared
        declared += 1
        return heads(distribution)

    monkeypatch.setattr(FastAPI, "openapi", counted)
    monkeypatch.setattr(api_tools_module, "parse_openapi_to_http_routes", counted_routes)
    monkeypatch.setattr(app_module, "heads", counted_heads)
    first = service_app(settings=settings)
    assert first.openapi() == expected
    assert [tool.model_dump() for tool in created_tools[-1]] == expected_tools
    # Mutating one application's public tool schemas must not corrupt the cached definitions.
    for tool in created_tools[-1]:
        tool.parameters.clear()
        assert tool.output_schema is not None
        for body in tool.output_schema["properties"]["body"]["anyOf"]:
            body.clear()
    first.openapi()["paths"].clear()
    first.openapi()["components"]["schemas"].clear()
    warm_counts = generated, parsed, declared
    second = service_app(role="all", settings=settings)
    assert second is not first
    assert second.openapi() == expected
    assert (generated, parsed, declared) == warm_counts
    assert [tool.model_dump() for tool in created_tools[-1]] == expected_tools
    assert app_module.build_mcp is build_mcp
    assert api_tools_module.parse_openapi_to_http_routes is counted_routes
    assert app_module.heads is counted_heads

    # The fixture must not patch ordinary construction or hide changes from contract tests.
    direct = build_app(role="control", settings=settings)
    assert (generated, parsed, declared) == tuple(count + 1 for count in warm_counts)
    assert direct.openapi() == expected


def test_service_apps_compile_extensions_and_preserve_worker_roles(
    service_app: Callable[..., FastAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings()
    service_app(settings=settings)  # Warm the OSS cache before introducing another API composition.
    router = APIRouter(prefix="/api/v1/fixture")

    @router.get("/extension", operation_id="fixture_extension", openapi_extra={"x-a13n-mcp": True})
    async def extension() -> dict[str, str]:
        return {"name": "extension"}

    distribution = OSS.extend(Distribution(name="fixture", routers=(router,), authenticator=LocalAuthenticator()))
    expected = build_app(distribution, role="control", settings=settings).openapi()
    extended = service_app(distribution, settings=settings)
    assert extended.openapi() == expected
    assert "/api/v1/fixture/extension" in extended.openapi()["paths"]
    assert "/api/v1/fixture/extension" not in service_app(settings=settings).openapi()["paths"]

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Worker construction must not assemble MCP")

    monkeypatch.setattr(app_module, "build_mcp", unexpected)
    worker = service_app(role="worker", settings=settings)
    assert worker.openapi_schema is None
    assert {route.path for route in worker.routes} == {"/healthz", "/readyz"}


@pytest.mark.anyio
@pytest.mark.parametrize("root_path", ["", "/mounted"])
async def test_router_prefilter_preserves_dispatch_and_app_local_overrides(root_path: str) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        events.append("start")
        yield
        events.append("stop")

    router = APIRouter(prefix="/prefix", lifespan=lifespan)

    def value() -> str:
        return "original"

    @router.get("/items/special")
    async def special() -> dict[str, str]:
        return {"item": "special"}

    @router.get("/items/{item_id:int}")
    async def item(item_id: int, dependency: Annotated[str, Depends(value)]) -> dict:
        return {"item": item_id, "value": dependency}

    @router.post("/files/{path:path}")
    async def file(path: str, body: dict[str, int]) -> dict:
        return {"path": path, "body": body}

    @router.post("/items/{item_id:int}")
    async def update(item_id: int) -> dict:
        return {"updated": item_id}

    direct, filtered = FastAPI(), FastAPI()
    direct.include_router(router)
    included = _include_individual_routes(filtered, router, FastAPI.include_router)
    assert filtered.openapi() == direct.openapi()
    for app in (direct, filtered):
        app.dependency_overrides[value] = lambda: "overridden"
    filtered.router.routes[:] = [
        _MatchedRouter(route, included[id(route)]) if id(route) in included else route for route in filtered.routes
    ]
    assert filtered.url_path_for("item", item_id=7) == direct.url_path_for("item", item_id=7)
    cases = [
        ("GET", "/items/special", None, 200),
        ("GET", "/items/7", None, 200),
        ("POST", "/items/7", None, 200),
        ("GET", "/items/7/", None, 307),
        ("DELETE", "/items/7", None, 405),
        ("GET", "/items/not-an-int", None, 404),
        ("GET", "/unknown", None, 404),
        ("POST", "/files/a/b.txt", {"count": 1}, 200),
        ("POST", "/files/a/b.txt", {"count": "invalid"}, 422),
    ]
    async with (
        direct.router.lifespan_context(direct),
        filtered.router.lifespan_context(filtered),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=direct, root_path=root_path), base_url="https://service.test"
        ) as expected,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=filtered, root_path=root_path), base_url="https://service.test"
        ) as actual,
    ):
        for method, path, body, status in cases:
            left = await expected.request(method, root_path + "/prefix" + path, json=body)
            right = await actual.request(method, root_path + "/prefix" + path, json=body)
            assert right.status_code == left.status_code == status
            assert right.content == left.content
            assert dict(right.headers) == dict(left.headers)
        filtered.dependency_overrides[value] = lambda: "second"
        assert (await actual.get(root_path + "/prefix/items/7")).json()["value"] == "second"
        assert (await expected.get(root_path + "/prefix/items/7")).json()["value"] == "overridden"
        assert events == ["start", "start"]
    assert events == ["start", "start", "stop", "stop"]


def test_router_prefilter_does_not_enter_unmatched_routers(monkeypatch: pytest.MonkeyPatch) -> None:
    router = APIRouter()

    @router.get("/items/{item_id}")
    async def item(item_id: str) -> dict:
        return {"id": item_id}

    app = FastAPI()
    app.include_router(router)
    included = app.routes[-1]
    filtered = _MatchedRouter(included, tuple(router.routes))
    calls = []
    original = included.matches

    def matches(scope):
        calls.append(scope["path"])
        return original(scope)

    monkeypatch.setattr(included, "matches", matches)
    scope = {"type": "http", "root_path": "", "method": "GET", "path": "/elsewhere"}
    assert filtered.matches(scope) == (Match.NONE, {})
    assert calls == []
    assert filtered.matches({**scope, "path": "/items/one"})[0] == Match.FULL
    assert filtered.matches({**scope, "path": "/items/one", "method": "DELETE"})[0] == Match.PARTIAL
    assert calls == ["/items/one", "/items/one"]


def test_individual_routes_leave_unrequested_siblings_uncompiled(monkeypatch: pytest.MonkeyPatch) -> None:
    router = APIRouter()

    @router.get("/first")
    async def first() -> dict:
        return {}

    @router.get("/second")
    async def second() -> dict:
        return {}

    app = FastAPI()
    included = _include_individual_routes(app, router, FastAPI.include_router)
    first_route, second_route = app.routes[-2:]

    def unexpected(scope):
        pytest.fail("Matching the first route must not enter its sibling's include context")

    monkeypatch.setattr(second_route, "matches", unexpected)
    scope = {"type": "http", "root_path": "", "method": "GET", "path": "/first"}
    assert _MatchedRouter(first_route, included[id(first_route)]).matches(scope)[0] == Match.FULL
    assert _MatchedRouter(second_route, included[id(second_route)]).matches(scope) == (Match.NONE, {})

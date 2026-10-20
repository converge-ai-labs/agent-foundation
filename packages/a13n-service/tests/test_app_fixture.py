"""Test-only contract reuse preserves production assembly and per-application isolation."""

from collections.abc import Callable
from typing import Any

import pytest
from a13n_service import api_tools as api_tools_module
from a13n_service import app as app_module
from a13n_service import mcp as mcp_module
from a13n_service.app import build_app
from a13n_service.distribution import OSS, Distribution
from a13n_service.mcp import build_mcp
from a13n_service.settings import Settings
from a13n_service.tenancy.authenticate import LocalAuthenticator
from fastapi import APIRouter, FastAPI
from fastmcp.tools import Tool
from fastmcp.utilities.openapi import HTTPRoute


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

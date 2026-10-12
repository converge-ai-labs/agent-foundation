"""Test-only contract reuse preserves production assembly and per-application isolation."""

from collections.abc import Callable
from typing import Any

import pytest
from a13n_service import app as app_module
from a13n_service.app import build_app
from a13n_service.distribution import OSS, Distribution
from a13n_service.mcp import build_mcp
from a13n_service.settings import Settings
from a13n_service.tenancy.authenticate import LocalAuthenticator
from fastapi import APIRouter, FastAPI


def test_service_apps_copy_the_contract_without_recompiling_it(
    service_app: Callable[..., FastAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings()
    expected = build_app(role="control", settings=settings).openapi()
    original = FastAPI.openapi
    generated = 0

    def counted(app: FastAPI) -> dict[str, Any]:
        nonlocal generated
        generated += 1
        return original(app)

    monkeypatch.setattr(FastAPI, "openapi", counted)
    first = service_app(settings=settings)
    assert first.openapi() == expected
    first.openapi()["paths"].clear()
    first.openapi()["components"]["schemas"].clear()
    warm_count = generated
    second = service_app(role="all", settings=settings)
    assert second is not first
    assert second.openapi() == expected
    assert generated == warm_count
    assert app_module.build_mcp is build_mcp

    # The fixture must not patch ordinary construction or hide changes from contract tests.
    direct = build_app(role="control", settings=settings)
    assert generated == warm_count + 1
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

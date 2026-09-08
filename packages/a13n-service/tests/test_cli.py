import asyncio

import httpx2
import pytest
from a13n_service.cli import main
from a13n_service.settings import get_settings
from click.testing import CliRunner
from fastapi import FastAPI


def test_serve_role_overrides_environment_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("A13N_SERVICE_ROLE", "all")
    get_settings.cache_clear()
    served_apps: list[FastAPI] = []

    def capture_app(app: FastAPI, **kwargs: object) -> None:
        del kwargs
        served_apps.append(app)

    monkeypatch.setattr("a13n_service.cli.uvicorn.run", capture_app)
    try:
        result = CliRunner().invoke(main, ["serve", "--role", "worker"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0, result.output
    assert len(served_apps) == 1

    async def read_health() -> httpx2.Response:
        transport = httpx2.ASGITransport(app=served_apps[0])
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/healthz")

    assert asyncio.run(read_health()).json() == {"status": "ok", "role": "worker"}


def test_database_cli_delegates_to_service_migrator(monkeypatch: pytest.MonkeyPatch) -> None:
    revisions: list[str] = []

    class Migrator:
        def upgrade(self, revision: str) -> None:
            revisions.append(revision)

    monkeypatch.setattr("a13n_service.cli._migrator", lambda: Migrator())

    result = CliRunner().invoke(main, ["db", "upgrade", "--revision", "head"])

    assert result.exit_code == 0, result.output
    assert revisions == ["head"]

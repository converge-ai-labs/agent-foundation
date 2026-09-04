"""Shared process-composition test setup."""

import asyncio
from base64 import b64encode
from pathlib import Path

import httpx2
from a13n_service.database import DatabaseMigrator
from a13n_service.settings import ServiceSettings
from fastapi import FastAPI


def request(app: FastAPI, path: str, *, method: str = "GET") -> httpx2.Response:
    async def send_request() -> httpx2.Response:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path)

    return asyncio.run(send_request())


def create_web_dist(directory: Path) -> Path:
    assets = directory / "assets"
    assets.mkdir(parents=True)
    (directory / "index.html").write_text("<!doctype html><title>Foundation Web</title>", encoding="utf-8")
    (assets / "app.js").write_text('document.title = "Foundation Web";', encoding="utf-8")
    return directory


def local_settings(tmp_path: Path, **updates: object) -> ServiceSettings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_backend": "sqlite",
        "database_sqlite_path": tmp_path / "database.sqlite3",
        "redis_backend": "memory",
        "object_backend": "local",
        "object_local_root": tmp_path / "objects",
        "filesystem_root": tmp_path / "files",
        "secret_master_key_base64": b64encode(b"0123456789abcdef0123456789abcdef").decode(),
        "secret_encryption_key_id": "foundation-service-test-key",
        "connectivity_public_origin": "http://testserver",
        "connectivity_http_origins": ("http://testserver",),
    }
    values.update(updates)
    settings = ServiceSettings(**values)
    DatabaseMigrator(settings.database_config()).upgrade()
    return settings


__all__ = ["create_web_dist", "local_settings", "request"]

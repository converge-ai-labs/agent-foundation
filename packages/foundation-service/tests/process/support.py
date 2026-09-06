"""Shared process-composition test setup."""

import asyncio
from base64 import b64encode
from pathlib import Path
from shutil import copyfile

import httpx2
from a13n_service.database import DatabaseMigrator
from a13n_service.settings import Settings
from fastapi import FastAPI


def request(app: FastAPI, path: str, *, method: str = "GET") -> httpx2.Response:
    async def send_request() -> httpx2.Response:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path)

    return asyncio.run(send_request())


def local_settings(tmp_path: Path, *, database_template: Path | None = None, **updates: object) -> Settings:
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
    settings = Settings(**values)
    if (
        database_template is not None
        and settings.database_backend == "sqlite"
        and not settings.database_sqlite_path.exists()
    ):
        settings.database_sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        copyfile(database_template, settings.database_sqlite_path)
    else:
        DatabaseMigrator(settings.database_config()).upgrade()
    return settings


__all__ = ["local_settings", "request"]

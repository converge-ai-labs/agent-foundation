"""Shared process-composition test setup."""

import asyncio
from base64 import b64encode
from collections.abc import Callable
from pathlib import Path

import httpx2
from a13n_service.configuration.sources import configuration_fields
from a13n_service.settings import Settings
from fastapi import FastAPI


def request(app: FastAPI, path: str, *, method: str = "GET") -> httpx2.Response:
    async def send_request() -> httpx2.Response:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path)

    return asyncio.run(send_request())


def local_settings(tmp_path: Path, *, database_url_factory: Callable[[], str], **updates: object) -> Settings:
    values: dict[str, object] = {
        "database_url": database_url_factory(),
        "redis_backend": "memory",
        "object_backend": "local",
        "object_local_root": tmp_path / "objects",
        "filesystem_root": tmp_path / "files",
        "secret_master_key_base64": b64encode(b"0123456789abcdef0123456789abcdef").decode(),
        "secret_encryption_key_id": "a13n-service-test-key",
        "connectivity_public_origin": "http://127.0.0.1",
        "connectivity_http_origins": ("http://127.0.0.1",),
        "iam_initial_admin_email": "admin@example.com",
        "iam_public_origin": "https://testserver",
    }
    values.update(updates)
    nested = {}
    for env_name, path, _ in configuration_fields(Settings):
        key = env_name.removeprefix("A13N_SERVICE_").lower()
        if key not in values:
            continue
        group = nested
        for part in path[:-1]:
            group = group.setdefault(part, {})
        group[path[-1]] = values.pop(key)
    assert not values, f"Unknown test configuration overrides: {list(values)}"
    return Settings.model_validate(nested)


__all__ = ["local_settings", "request"]

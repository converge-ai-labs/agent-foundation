"""Test servers signal successful or failed startup and preserve application lifespan ownership."""

from contextlib import asynccontextmanager

import httpx2
import pytest
from fastapi import FastAPI

from .http_support import serve_http

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("lifespan", ["on", "off"])
async def test_http_server_is_ready_and_lifespan_is_owned_once(lifespan):
    entered, exited = [], []

    @asynccontextmanager
    async def lifetime(app):
        entered.append(app)
        yield
        exited.append(app)

    app = FastAPI(lifespan=lifetime)

    @app.get("/ready")
    async def ready():
        return {"ready": True}

    async with serve_http(app, lifespan=lifespan) as url:
        assert len(entered) == (lifespan == "on")
        assert exited == []
        async with httpx2.AsyncClient() as client:
            response = await client.get(url + "/ready")
        assert response.json() == {"ready": True}
    assert exited == entered


async def test_failed_http_startup_wakes_the_caller():
    @asynccontextmanager
    async def lifetime(app):
        raise RuntimeError("startup failed")
        yield

    with pytest.raises(RuntimeError, match="exited before startup completed"):
        async with serve_http(FastAPI(lifespan=lifetime), lifespan="on"):
            pytest.fail("A server whose startup failed must not be yielded")

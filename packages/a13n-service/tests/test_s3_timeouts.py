"""Object deadlines are dependency failures; caller cancellation must still propagate."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import anyio
import httpx2
import pytest
from a13n_logging import log_context
from a13n_logging.context import ContextFilter
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import install_error_envelope
from a13n_service.infra.objects.s3 import S3Objects
from fastapi import FastAPI, Request

from .http_support import serve_http

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def capture_context(caplog):
    # Production logging installs this filter; pytest owns its own handler.
    context = ContextFilter()
    caplog.handler.addFilter(context)
    try:
        yield
    finally:
        caplog.handler.removeFilter(context)


async def blocked(**kwargs):
    await anyio.sleep_forever()


async def invoke(store, operation):
    if operation == "put":
        return await store.put("private-key", b"private-body", content_type="text/plain")
    if operation == "list":
        return await store.keys("private-prefix", limit=1)
    return await getattr(store, operation)("private-key")


def slow_store(timeout=0.01):
    client = AsyncMock()
    for name in ("put_object", "get_object", "list_objects_v2", "delete_object"):
        getattr(client, name).side_effect = blocked
    return S3Objects(client, "private-bucket", prefix="", max_bytes=100, timeout=timeout)


@pytest.mark.parametrize("operation", ["get", "put", "list", "delete"])
async def test_deadline_maps_to_unavailable_with_safe_diagnostics(operation, caplog):
    with log_context(request_id="req_test_timeout"):
        with pytest.raises(ServiceError) as error:
            await invoke(slow_store(), operation)
    assert error.value.code == "unavailable"
    assert error.value.details == {"dependency": "objects"}
    record = next(r for r in caplog.records if r.message == "Object store request timed out")
    assert record.operation == operation
    assert record.timeout_seconds == 0.01
    assert record.duration_ms >= 0
    assert record.request_id == "req_test_timeout"
    assert "private-" not in caplog.text


@pytest.mark.parametrize("operation", ["get", "put", "list", "delete"])
async def test_caller_cancellation_is_not_a_dependency_failure(operation, caplog):
    started = asyncio.Event()
    store = slow_store(timeout=10)

    async def waiting(**kwargs):
        started.set()
        await anyio.sleep_forever()

    for name in ("put_object", "get_object", "list_objects_v2", "delete_object"):
        getattr(store.client, name).side_effect = waiting
    task = asyncio.create_task(invoke(store, operation))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not any(r.message == "Object store request timed out" for r in caplog.records)


async def test_get_body_is_inside_deadline_and_closed():
    closed = False
    body = AsyncMock()
    body.read.side_effect = blocked

    @asynccontextmanager
    async def stream():
        nonlocal closed
        try:
            yield body
        finally:
            closed = True

    client = AsyncMock()
    client.get_object.return_value = {"ContentLength": 10, "Body": stream()}
    store = S3Objects(client, "bucket", prefix="", max_bytes=100, timeout=0.01)
    with pytest.raises(ServiceError) as error:
        await store.get("key")
    assert error.value.code == "unavailable"
    assert closed


async def test_timeout_returns_503_and_preserves_http_connection():
    app = FastAPI()
    install_error_envelope(app)
    peers = []

    @app.get("/probe")
    async def probe(request: Request):
        peers.append(request.client)
        if len(peers) == 1:
            await slow_store().get("key")
        return {"ok": True}

    async with serve_http(app) as url, httpx2.AsyncClient(base_url=url, trust_env=False) as client:
        response = await client.get("/probe")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "unavailable"
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
        assert response.headers.get("connection") != "close"
        assert (await client.get("/probe")).json() == {"ok": True}
    assert peers[0] == peers[1]

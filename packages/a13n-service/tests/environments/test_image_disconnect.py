"""An actual HTTP socket close cancels an in-flight image request."""

import asyncio
import socket

import pytest
from a13n_service.environments import router as environment_router
from a13n_service.iam import authenticate_request
from fastapi import FastAPI
from uvicorn import Config, Server

from .conftest import actor

pytestmark = pytest.mark.anyio


async def test_network_disconnect_cancels_image_work(monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class ImageService:
        async def test_docker_image(self, **_kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    monkeypatch.setattr(environment_router, "_service", lambda _request: ImageService())
    app = FastAPI()
    app.dependency_overrides[authenticate_request] = actor
    app.include_router(environment_router.router)
    server = Server(Config(app, lifespan="off", log_level="error"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        writer = None
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    assert not serving.done()
                    await asyncio.sleep(0.01)
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                body = (
                    b'{"request_id":"envtest_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","workspace_id":null,"configuration":{}}'
                )
                writer.write(
                    b"POST /api/v1/environment-providers/envp_test/test-image HTTP/1.1\r\n"
                    b"Host: 127.0.0.1\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
                await entered.wait()
                writer.close()
                await writer.wait_closed()
                await cancelled.wait()
        finally:
            if writer is not None:
                writer.close()
            server.should_exit = True
            await asyncio.wait_for(serving, 5)

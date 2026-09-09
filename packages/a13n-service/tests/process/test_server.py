import asyncio
import socket

import httpx2
import pytest
from a13n_service.app import create_app
from a13n_service.process.server import ServiceServer
from anyio import Event, fail_after

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("request_finishes", [True, False])
async def test_server_drains_application_before_waiting_for_active_http(local_settings, tmp_path, request_finishes):
    app = create_app(local_settings(tmp_path))
    entered, release = Event(), Event()

    @app.get("/active-request")
    async def active_request():
        entered.set()
        await release.wait()
        return {"completed": True}

    server = ServiceServer(app)
    # Exercise the production transport timeout without a 30-second test wait.
    server.config.timeout_graceful_shutdown = 0.2
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            with fail_after(10):
                while not server.started:
                    assert not serving.done(), "Server exited before readiness"
                    await asyncio.sleep(0.01)
                async with httpx2.AsyncClient(base_url=origin, trust_env=False) as client:
                    request = asyncio.create_task(client.get("/active-request"))
                    try:
                        await entered.wait()
                        # This is also the state transition made by Uvicorn's SIGTERM handler.
                        server.should_exit = True
                        while not app.state.runtime.status.draining:
                            await asyncio.sleep(0.01)

                        runtime = app.state.runtime
                        assert runtime.status.startup_complete
                        assert runtime.worker.execution_loop.is_draining()
                        assert runtime.worker.environment_maintenance.is_draining()
                        assert runtime.control.subagent_maintenance.is_draining()
                        assert not request.done(), "HTTP finished before application drain began"

                        # The listener is closing; verify the application's response to an
                        # already accepted request without depending on TCP accept timing.
                        async with httpx2.AsyncClient(
                            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
                        ) as admitted_client:
                            assert (await admitted_client.get("/readyz")).status_code == 503
                            assert (await admitted_client.get("/api/v1/models")).status_code == 503

                        if request_finishes:
                            release.set()
                        response = await request
                        assert response.status_code == (200 if request_finishes else 500)
                        await serving
                        assert not runtime.status.startup_complete
                    finally:
                        release.set()
                        if not request.done():
                            request.cancel()
                        await asyncio.gather(request, return_exceptions=True)
        finally:
            release.set()
            server.should_exit = True
            await asyncio.wait_for(serving, 5)

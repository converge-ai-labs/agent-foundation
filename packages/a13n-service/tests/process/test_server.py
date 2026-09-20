import asyncio
import socket

import httpx2
import pytest
import uvicorn
from a13n_service.app import create_app
from a13n_service.process.server import ServiceServer
from anyio import Event, fail_after

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("request_finishes", [True, False])
async def test_server_drains_application_before_waiting_for_active_http(
    local_settings, tmp_path, request_finishes, monkeypatch
):
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
    transport_entered, release_transport = Event(), Event()
    shutdown = uvicorn.Server.shutdown

    async def observe_transport_shutdown(self, *args, **kwargs):
        # Pause at the real application-to-transport boundary so the test can
        # inspect drain ordering before the transport's short timer starts.
        transport_entered.set()
        await release_transport.wait()
        await shutdown(self, *args, **kwargs)

    monkeypatch.setattr(uvicorn.Server, "shutdown", observe_transport_shutdown)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            with fail_after(30):
                while not server.started:
                    assert not serving.done(), "Server exited before readiness"
                    await asyncio.sleep(0.01)
                async with httpx2.AsyncClient(
                    base_url=origin, trust_env=False, timeout=None, limits=httpx2.Limits(max_keepalive_connections=0)
                ) as client:
                    request = asyncio.create_task(client.get("/active-request"))
                    try:
                        await entered.wait()
                        # This is also the state transition made by Uvicorn's SIGTERM handler.
                        server.should_exit = True
                        await transport_entered.wait()

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
                        release_transport.set()
                        response = await request
                        assert response.status_code == (200 if request_finishes else 500)
                        await serving
                        assert not runtime.status.startup_complete
                    finally:
                        release_transport.set()
                        release.set()
                        if not request.done():
                            request.cancel()
                        await asyncio.gather(request, return_exceptions=True)
        finally:
            release_transport.set()
            release.set()
            server.should_exit = True
            await asyncio.wait_for(serving, 30)


@pytest.mark.parametrize("returns_normally", [False, True])
async def test_critical_component_failure_after_readiness_stops_http(
    local_settings, tmp_path, monkeypatch, returns_normally
):
    from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop

    fail_component = Event()

    async def component(self):
        try:
            await fail_component.wait()
            if not returns_normally:
                raise RuntimeError("injected critical component failure")
        finally:
            self._stopped.set()

    monkeypatch.setattr(EnvironmentMaintenanceLoop, "run", component)
    app = create_app(local_settings(tmp_path))
    server = ServiceServer(app)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            with fail_after(30):
                while not server.started:
                    assert not serving.done(), "Server exited before startup"
                    await asyncio.sleep(0.01)
                async with httpx2.AsyncClient(
                    base_url=origin, trust_env=False, timeout=None, limits=httpx2.Limits(max_keepalive_connections=0)
                ) as client:
                    assert (await client.get("/readyz")).status_code == 200
                    fail_component.set()
                    await serving
                    assert not app.state.runtime.status.startup_complete
                    assert app.state.runtime.status.draining
                    with pytest.raises(httpx2.ConnectError):
                        await client.get("/healthz")
        finally:
            fail_component.set()
            server.should_exit = True
            await asyncio.wait_for(serving, 30)


@pytest.mark.parametrize("failed", [False, True])
async def test_entrypoint_exit_code_reports_lifespan_failure(local_settings, tmp_path, monkeypatch, failed):
    from types import SimpleNamespace

    from a13n_service.process.server import serve_app

    def run(server):
        server.lifespan = SimpleNamespace(error_occurred=failed)

    monkeypatch.setattr(ServiceServer, "run", run)
    app = create_app(local_settings(tmp_path))
    if failed:
        with pytest.raises(SystemExit) as caught:
            serve_app(app)
        assert caught.value.code == 1
    else:
        serve_app(app)

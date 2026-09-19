"""Device enrollment uses narrow credentials and persists only approved trust."""

from contextlib import asynccontextmanager

import httpx
import pytest
from a13n_harness.providers.environment.remote_envd.pairing import (
    MAX_PENDING_PAIRINGS,
    PairingRequest,
    credential_digest,
)
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.device_pairing import DevicePairings
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.webui import create_webui

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio
TOKEN = "a" * 64
REQUEST = {"device_id": "native-paired", "name": "My workstation"}


@asynccontextmanager
async def api(tmp_path):
    server = create_webui(
        lambda: open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=tmp_path / "a13n-harness-ui.yaml"),
        api_key="browser-key",
    )
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://localhost") as client,
    ):
        yield client


async def pair(client, token=TOKEN, request=None):
    return await client.post("/api/envd/pair", headers={"Authorization": f"Bearer {token}"}, json=request or REQUEST)


async def test_pair_approve_restart_and_revoke(tmp_path):
    _write_configuration(tmp_path)
    browser = {"Authorization": "Bearer browser-key"}
    async with api(tmp_path) as client:
        pending = await pair(client)
        assert pending.status_code == 200, pending.text
        challenge = pending.json()["challenge"]
        key = challenge["pairing_id"]
        assert (await pair(client)).json() == pending.json()
        assert (await client.get("/api/devices", headers=browser)).json() == []
        assert (await client.get("/api/device-pairings", headers=browser)).json() == [challenge]
        approve = f"/api/device-pairings/{key}/approve"
        # Device credentials cannot grant approval or ordinary browser access.
        assert (await client.post(approve, headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 401
        approved = await client.post(approve, headers=browser)
        assert approved.status_code == 200, approved.text
        resource = approved.json()
        assert resource["registration"] == "paired"
        assert (await client.post(approve, headers=browser)).json() == resource
        assert (await client.get("/api/device-pairings", headers=browser)).json() == []
        connected = await pair(client)
        assert connected.json() == {
            "status": "approved",
            "resource_id": resource["id"],
            "websocket_url": f"ws://localhost/api/devices/{resource['id']}/connect",
        }
        for response in (pending, approved, connected):
            assert TOKEN not in response.text
            assert credential_digest(TOKEN) not in response.text
        source = (tmp_path / "devices" / f"{resource['id']}.yaml").read_text()
        assert TOKEN not in source
        assert credential_digest(TOKEN) in source
        assert (await pair(client, request={**REQUEST, "device_id": "other"})).status_code == 409
    async with api(tmp_path) as client:
        assert (await pair(client)).json() == connected.json()
        revoked = await client.post(f"/api/devices/{resource['id']}/revoke", headers=browser)
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["registration"] == "revoked"
        assert (await pair(client)).status_code == 403
        assert (await client.post(approve, headers=browser)).status_code == 403
    async with api(tmp_path) as client:
        assert (await pair(client)).status_code == 403
        info = await client.get(f"/api/devices/{resource['id']}", headers=browser)
        assert info.json()["registration"] == "revoked"
        assert info.json()["available"] is False
        assert info.json()["error_code"] == "device_revoked"


async def test_pairing_boundary_and_rejection(tmp_path):
    _write_configuration(tmp_path)
    browser = {"Authorization": "Bearer browser-key"}
    async with api(tmp_path) as client:
        for token in ("", "browser-key", "short", "A" * 64):
            rejected = await pair(client, token)
            assert rejected.status_code == 403
        assert (await client.post("/api/envd/pair", json=REQUEST)).status_code == 403
        assert (await client.get("/api/envd/pair")).status_code == 401
        assert (await client.get("/api/device-pairings")).status_code == 401
        cross_origin = await client.post(
            "/api/envd/pair",
            json=REQUEST,
            headers={"Origin": "https://elsewhere.invalid", "Authorization": f"Bearer {TOKEN}"},
        )
        assert cross_origin.status_code == 403
        pending = await pair(client)
        key = pending.json()["challenge"]["pairing_id"]
        assert (await pair(client, request={**REQUEST, "name": "changed"})).status_code == 409
        assert (await client.post(f"/api/device-pairings/{key}/reject", headers=browser)).status_code == 204
        assert (await pair(client)).status_code == 403
        assert (await client.get("/api/device-pairings", headers=browser)).json() == []
        assert not (tmp_path / "devices").exists()


def test_pending_pairings_are_bounded_and_expire():
    from datetime import UTC, datetime, timedelta

    pairings = DevicePairings()
    request = PairingRequest(**REQUEST)
    for number in range(MAX_PENDING_PAIRINGS):
        pairings.poll(request, f"{number:064x}", {}, origin="http://localhost")
    with pytest.raises(HarnessUiError, match="Too many"):
        pairings.poll(request, TOKEN, {}, origin="http://localhost")
    key, pending = next(iter(pairings._pending.items()))
    pairings._pending[key] = pending.model_copy(
        update={
            "challenge": pending.challenge.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
        }
    )
    pairings.poll(request, TOKEN, {}, origin="http://localhost")
    assert len(pairings.list_pending()) == MAX_PENDING_PAIRINGS


async def test_connect_command_approved_by_webui_and_live_revocation(tmp_path, caplog):
    import asyncio
    import os
    from pathlib import Path

    from a13n_harness_ui.composition.models import ResolvedEnvironmentBinding
    from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection

    from .test_device_runtime import app_listener, device_path

    executable = os.environ.get("A13N_ENVD_TEST_BINARY")
    if executable is None:
        pytest.skip("Set A13N_ENVD_TEST_BINARY for real Device integration tests")
    remote = tmp_path / "remote"
    remote.mkdir()
    async with app_listener(tmp_path) as (app, http, _ws):
        process = await asyncio.create_subprocess_exec(
            str(Path(executable).resolve()),
            "connect",
            http,
            "--host",
            "test-host",
            "--state-dir",
            str(tmp_path / "envd-state"),
            "--name",
            "Paired workstation",
            "--default-working-directory",
            str(remote),
            env={
                **{key: value for key, value in os.environ.items() if not key.startswith("A13N_ENVD_")},
                "A13N_ENVD_FULL_CONTROL": "1",
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with httpx.AsyncClient(
                base_url=http,
                headers={"Authorization": "Bearer test-only-key"},
                trust_env=False,
            ) as client:
                async with asyncio.timeout(15):
                    while not (pending := (await client.get("/api/device-pairings")).json()):
                        if process.returncode is not None:
                            _, stderr = await process.communicate()
                            pytest.fail(stderr.decode(errors="replace"))
                        await asyncio.sleep(0.03)
                approved = await client.post(f"/api/device-pairings/{pending[0]['pairing_id']}/approve")
                assert approved.status_code == 200, approved.text
                resource_id = approved.json()["id"]
                async with asyncio.timeout(15):
                    while not (await app.device_info(resource_id)).available:
                        await asyncio.sleep(0.03)
                recipe = (await app.current_configuration()).devices[resource_id]
                captured = ResolvedEnvironmentBinding(
                    device=recipe,
                    selection=EnvironmentBindingSelection(
                        device_id=resource_id,
                        alias="remote",
                        working_directory=device_path(remote),
                    ),
                )
                environment = await app._devices.bind(captured, environment_id="paired-test")
                try:
                    await environment.prepare()
                    files = environment.operations.files
                    assert files is not None
                    await files.write_text(device_path(remote / "retained.txt"), "paired", mode="create")
                    # An already authenticated but not yet attached carrier must also be fenced.
                    secret = (tmp_path / "envd-state/instances/default/hosts/test-host/credential").read_text().strip()
                    attachment = await app.authenticate_device_attachment(resource_id, secret)
                    revoked = await client.post(f"/api/devices/{resource_id}/revoke")
                    assert revoked.status_code == 200, revoked.text
                    with pytest.raises(HarnessUiError, match="revoked"):
                        await app._devices.bind(captured, environment_id="after-revoke")
                    assert attachment.connections._closed
                    async with asyncio.timeout(10):
                        await process.wait()
                    assert process.returncode != 0
                    assert (remote / "retained.txt").read_text() == "paired"
                    assert (
                        tmp_path / "envd-state/instances/default/hosts/test-host/credential"
                    ).read_text().strip() == secret
                    assert (await client.get("/api/device-pairings")).json() == []
                finally:
                    await environment.close()
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), 5)
            except TimeoutError:
                process.kill()
                await process.communicate()
                raise
            assert b"Traceback" not in stderr
    assert "Exception in ASGI application" not in caplog.text


async def test_device_attachment_preserves_handler_cancellation(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from a13n_harness.providers.environment.remote_envd.connections import WebSocketEnvdConnections
    from a13n_harness_ui.devices import DeviceAttachment

    connections = WebSocketEnvdConnections()
    attachment = DeviceAttachment("native-test", connections)
    entered = asyncio.Event()

    async def wait_for_close(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(connections, "attach", wait_for_close)
    task = asyncio.create_task(attachment.attach(AsyncMock()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await connections.close()

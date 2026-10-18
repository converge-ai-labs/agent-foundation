"""Opt-in enforcement on an operator-built snapshot, through Daytona's signed port route."""

import asyncio
import json
import os
import secrets
from urllib.parse import quote

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.daytona.shared import DaytonaConnectionConfiguration
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.providers.environments.daytona.configuration import DaytonaRecipe
from a13n_service.providers.environments.daytona.management import DaytonaManagement
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .desktop_access_rfb import RFB, AuthRejected

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.timeout(300),
    pytest.mark.skipif(
        os.environ.get("A13N_TEST_DAYTONA_DESKTOP") != "1" or not os.environ.get("A13N_TEST_DAYTONA_SNAPSHOT"),
        reason="Requires explicit Daytona desktop opt-in, credentials and an operator-built snapshot",
    ),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_signed_route_readonly_revocation_expiry_and_restart(tmp_path):
    try:
        runtime = NativeRuntime(
            DaytonaConnectionConfiguration.model_validate_json(os.environ["A13N_TEST_DAYTONA_BACKEND_JSON"]),
            TokenCredential.model_validate_json(os.environ["A13N_TEST_DAYTONA_CREDENTIAL_JSON"]),
        )
    except (KeyError, ValueError):
        pytest.fail("Configure the Daytona test backend and credential JSON", pytrace=False)
    management = DaytonaManagement(
        DaytonaRecipe(desktop=True, desktop_assistance=True, snapshot=os.environ["A13N_TEST_DAYTONA_SNAPSHOT"]),
        "env-daytona-access-" + secrets.token_hex(8),
        None,
        runtime,
        "op-create",
    )
    clients = []
    endpoints = {}
    evidence = {}
    try:
        await management.create()
        description = await management.desktop_access(wire.Describe())
        assert isinstance(description, wire.Description) and description.revocation_fences_grants
        assert description.websocket_url
        generation = description.generation
        target = await management.lookup()
        assert target is not None
        toolbox = await management.open_toolbox(target)

        async def position():
            return await toolbox.request("GET", target.id + "/computeruse/mouse/position")

        async def connect(identity, mode, epoch, ttl=60):
            route = await management.desktop_access(wire.Describe())
            assert isinstance(route, wire.Description) and route.generation == generation and route.websocket_url
            grant = await management.desktop_access(
                wire.Grant(
                    generation=generation,
                    id=identity,
                    mode=mode,
                    epoch=epoch,
                    ttl_seconds=ttl,
                )
            )
            assert isinstance(grant, wire.AccessGrant) and grant.token and grant.password
            endpoints[identity] = (
                route.websocket_url + "?token=" + quote(grant.token.get_secret_value(), safe=""),
                grant.password.get_secret_value(),
            )
            client = await asyncio.to_thread(RFB, *endpoints[identity])
            clients.append(client)
            await asyncio.to_thread(client.frame)
            return client

        viewer = await connect("dgrant_readonly", "view", 1)
        # Access operations own a separate short-lived toolbox client.
        toolbox = await management.open_toolbox(target)
        await toolbox.request("POST", target.id + "/computeruse/mouse/move", body={"x": 100, "y": 100})
        await asyncio.to_thread(viewer.pointer, 200, 200)
        # A frame response on the same ordered channel confirms that the
        # preceding pointer packet has reached the server, despite proxy delay.
        assert (await asyncio.to_thread(viewer.frame))["pixel_bytes"] > 0
        assert await position() == {"x": 100, "y": 100}
        evidence["server_readonly_enforced"] = True
        await management.desktop_access(wire.Renew(generation=generation, id="dgrant_readonly", epoch=1))
        controller = await connect("dgrant_control", "control", 2)
        toolbox = await management.open_toolbox(target)
        await asyncio.to_thread(controller.pointer, 300, 300)
        async with asyncio.timeout(10):
            while await position() != {"x": 300, "y": 300}:
                await asyncio.sleep(0.2)
        await management.desktop_access(wire.Renew(generation=generation, id="dgrant_readonly", epoch=1))
        with pytest.raises(EnvironmentProviderError) as conflict:
            await management.desktop_access(
                wire.Grant(
                    generation=generation,
                    id="dgrant_conflict",
                    mode="control",
                    epoch=3,
                )
            )
        assert conflict.value.code == "desktop_access_controller_exists"
        result = await management.desktop_access(
            wire.Inspect(
                operation="revoke",
                generation=generation,
                id="dgrant_control",
                epoch=2,
            )
        )
        assert isinstance(result, wire.AccessGrant) and result.state == "revoked"
        assert await asyncio.to_thread(controller.closed)
        # TLS/upgrade timeouts and proxy errors are inconclusive, not proof of
        # authorization rejection. They must fail this acceptance check.
        with pytest.raises((ConnectionClosed, InvalidStatus, AuthRejected, EOFError)) as rejected:
            await asyncio.to_thread(RFB, *endpoints["dgrant_control"])
        if isinstance(rejected.value, InvalidStatus):
            assert rejected.value.response.status_code in {401, 403}
        assert (await asyncio.to_thread(viewer.frame))["pixel_bytes"] > 0
        replay = await management.desktop_access(
            wire.Grant(
                generation=generation,
                id="dgrant_control",
                mode="control",
                epoch=2,
            )
        )
        assert isinstance(replay, wire.AccessGrant) and replay.state == "revoked" and replay.token is None
        evidence["existing_controller_disconnected_and_cannot_regrant"] = True
        (tmp_path / "daytona-access-evidence.json").write_text(json.dumps(evidence, indent=2))
        expiring = await connect("dgrant_expiring", "control", 3)
        await management.desktop_access(wire.Renew(generation=generation, id="dgrant_expiring", epoch=3, ttl_seconds=5))
        await asyncio.sleep(5)
        assert await asyncio.to_thread(expiring.closed)
        await management.desktop_access(wire.Settle(generation=generation))
        evidence["guest_expiry_without_host_renewal"] = True
        (tmp_path / "daytona-access-evidence.json").write_text(json.dumps(evidence, indent=2))
        await management.stop()
        await management.start()
        replacement = await management.desktop_access(wire.Describe())
        assert isinstance(replacement, wire.Description) and replacement.generation != generation
        assert await asyncio.to_thread(viewer.closed)
        with pytest.raises(EnvironmentProviderError) as stale:
            await management.desktop_access(
                wire.Grant(
                    generation=generation,
                    id="dgrant_stale",
                    mode="control",
                    epoch=4,
                )
            )
        assert stale.value.code == "desktop_access_stale_generation"
        evidence["restart_invalidates_old_generation"] = True
        (tmp_path / "daytona-access-evidence.json").write_text(json.dumps(evidence, indent=2))
    finally:
        for client in clients:
            await asyncio.to_thread(client.close)
        try:
            await management.destroy()
        finally:
            await management.close()

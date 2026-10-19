"""The guest endpoint owns input authority independently of a connected Service process."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError, EnvironmentProviderOutcomeCertainty
from a13n_harness.providers.environment.e2b.shared import E2BProviderRuntime
from a13n_service.providers.environment import desktop_access as protocol
from a13n_service.providers.environment import desktop_guest as guest
from a13n_service.providers.environments.e2b.configuration import E2BRecipe
from a13n_service.providers.environments.e2b.management import E2BManagement
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


@pytest.fixture
def supervisor(tmp_path, monkeypatch):
    instance = guest.Supervisor(tmp_path, ":99", "/private/Xauthority", 6081, "websockify")
    clock = [100.0]
    monkeypatch.setattr(guest.time, "monotonic", lambda: clock[0])
    commands = []

    class Process:
        returncode = None

        async def wait(self):
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    async def spawn(*command):
        commands.append(command)
        return Process()

    class Writer:
        def close(self):
            pass

        async def wait_closed(self):
            pass

    monkeypatch.setattr(instance, "spawn", spawn)
    monkeypatch.setattr(instance, "settle", AsyncMock())
    monkeypatch.setattr(guest.asyncio, "open_connection", AsyncMock(return_value=(None, Writer())))
    return instance, clock, commands


async def request(instance, operation, **fields):
    return await instance.dispatch(
        {"protocol_version": 1, "operation": operation, "generation": instance.generation, **fields}
    )


async def grant(instance, identity="control_first", mode="control", epoch=1, ttl=60):
    return await request(instance, "grant", id=identity, mode=mode, epoch=epoch, ttl_seconds=ttl)


async def test_revoke_is_idempotent_does_not_revoke_viewer_and_cannot_resurrect(supervisor):
    instance, _, commands = supervisor
    viewer = await grant(instance, "view_first", "view")
    controller = await grant(instance)
    assert "-viewonly" in commands[1]
    assert "-viewonly" not in commands[3]
    assert "-noremote" in commands[3] and "-noclipboard" in commands[3]
    assert controller["token"] not in repr(instance.grants["control_first"])
    assert controller["password"] not in repr(instance.grants["control_first"])
    assert (await grant(instance))["token"] == controller["token"]
    with pytest.raises(guest.Refused, match="controller_exists"):
        await grant(instance, "control_other", epoch=2)
    with pytest.raises(guest.Refused, match="grant_conflict"):
        await grant(instance, mode="view")
    revoked = await request(instance, "revoke", id="control_first", epoch=1)
    assert revoked["state"] == "revoked"
    assert "token" not in revoked and "password" not in revoked
    assert (await grant(instance))["state"] == "revoked"
    assert (await request(instance, "revoke", id="control_first", epoch=1)) == revoked
    instance.settle.assert_awaited_once()
    routes = (instance.root / "tokens").read_text()
    assert viewer["token"] in routes and controller["token"] not in routes
    assert instance.grants["view_first"].process.returncode is None
    with pytest.raises(guest.Refused, match="stale_epoch"):
        await grant(instance, "control_old", epoch=1)
    assert (await grant(instance, "control_new", epoch=2))["state"] == "active"


async def test_expiry_and_maximum_lifetime_are_enforced_before_renewal(supervisor):
    instance, clock, _ = supervisor
    await grant(instance)
    clock[0] = 160
    with pytest.raises(guest.Refused, match="grant_not_active"):
        await request(instance, "renew", id="control_first", epoch=1, ttl_seconds=60)
    assert instance.grants["control_first"].state == "expired"
    assert (instance.root / "tokens").read_text() == ""
    await grant(instance, "control_second", epoch=2)
    for now in range(210, 1060, 50):
        clock[0] = now
        await request(instance, "renew", id="control_second", epoch=2, ttl_seconds=60)
    clock[0] = 1060
    with pytest.raises(guest.Refused, match="grant_not_active"):
        await request(instance, "renew", id="control_second", epoch=2, ttl_seconds=60)


async def test_unconfirmed_cleanup_blocks_control_but_still_expires_other_grants(supervisor, monkeypatch):
    instance, clock, _ = supervisor
    controller = await grant(instance)
    await grant(instance, "view_first", "view", ttl=1)
    monkeypatch.setattr(instance, "settle", AsyncMock(side_effect=guest.Refused("input_cleanup_unconfirmed")))
    clock[0] = 161
    with pytest.raises(guest.Refused, match="input_cleanup_unconfirmed"):
        await instance.reap()
    assert instance.grants["control_first"].state == "revoking"
    assert instance.grants["control_first"].process.returncode == 0
    assert instance.grants["view_first"].state == "expired"
    assert controller["token"] not in (instance.root / "tokens").read_text()
    with pytest.raises(guest.Refused, match="input_cleanup_unconfirmed"):
        await grant(instance, "control_next", epoch=2)
    monkeypatch.setattr(instance, "settle", AsyncMock())
    await instance.reap()
    assert instance.grants["control_first"].state == "revoked"
    assert (await grant(instance, "control_next", epoch=2))["state"] == "active"


async def test_stale_generation_and_invalid_inputs_never_dispatch(supervisor):
    instance, _, commands = supervisor
    for version in (None, 0, 2, True, "1"):
        with pytest.raises(guest.Refused, match="unsupported_protocol_version"):
            await instance.dispatch({"protocol_version": version, "operation": "describe"})
    with pytest.raises(guest.Refused, match="unsupported_protocol_version"):
        await instance.dispatch({"operation": "describe"})
    with pytest.raises(guest.Refused, match="stale_generation"):
        await instance.dispatch({"protocol_version": 1, "operation": "grant", "generation": "old"})
    for ttl in (0, 61, True, 1.5, "30"):
        with pytest.raises(guest.Refused, match="invalid_ttl"):
            await grant(instance, ttl=ttl)
    for identity in ("../password", "a", "view\nother", "/tmp/password"):
        with pytest.raises(guest.Refused, match="invalid_grant_id"):
            await grant(instance, identity)
    assert commands == []


async def test_cancellation_during_startup_closes_backend(supervisor, monkeypatch):
    instance, _, _ = supervisor
    terminate = AsyncMock(wraps=instance.terminate)
    monkeypatch.setattr(instance, "terminate", terminate)
    monkeypatch.setattr(guest.asyncio, "open_connection", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await grant(instance)
    assert instance.grants == {}
    assert terminate.await_count == 2  # Password writer and unpublished backend.
    assert instance.epoch == 0


async def test_private_runtime_rejects_symlinks_and_world_access(tmp_path):
    directory = tmp_path / "runtime"
    guest.private_directory(directory)
    directory.chmod(0o755)
    with pytest.raises(guest.Refused, match="unsafe_runtime_directory"):
        guest.private_directory(directory)
    link = tmp_path / "link"
    link.symlink_to(directory)
    with pytest.raises(guest.Refused, match="unsafe_runtime_directory"):
        guest.private_directory(link)


async def test_wall_clock_expiry_handles_suspended_vm_clock(supervisor, monkeypatch):
    instance, _, _ = supervisor
    await grant(instance)
    monkeypatch.setattr(guest.time, "time", lambda: instance.grants["control_first"].expires_at + 1)
    await instance.reap()
    assert instance.grants["control_first"].state == "expired"


async def test_expiry_kills_input_even_while_management_lock_is_busy(supervisor):
    instance, clock, _ = supervisor
    await grant(instance)
    current = instance.grants["control_first"]
    async with instance.lock:
        clock[0] = 161
        instance.expire_input(current)
        assert current.process.returncode == -9
    await instance.reap()
    assert current.state == "expired"


async def test_protocol_redacts_credentials_and_rejects_wrong_reply_correlation():
    generation = "a" * 32
    command = protocol.Grant(generation=generation, id="grant_first", mode="control", epoch=3)
    response = {
        "generation": generation,
        "id": "grant_first",
        "mode": "control",
        "epoch": 3,
        "state": "active",
        "remaining_seconds": 30,
        "token": "private-token",
        "password": "private-password",
    }
    reply = protocol.decode("e2b", command, json.dumps({"result": response}))
    assert isinstance(reply, protocol.AccessGrant)
    assert "private-token" not in repr(reply) + reply.model_dump_json()
    for changed in (
        {"epoch": 4},
        {"generation": "b" * 32},
        {"id": "grant_other"},
        {"mode": "view"},
        {"state": "revoked"},
    ):
        with pytest.raises(EnvironmentProviderError) as raised:
            protocol.decode("e2b", command, json.dumps({"result": {**response, **changed}}))
        assert raised.value.certainty == EnvironmentProviderOutcomeCertainty.UNKNOWN
        assert "private" not in str(raised.value)
    with pytest.raises(EnvironmentProviderError):
        protocol.decode(
            "e2b",
            protocol.Inspect(operation="inspect", generation=generation, id="grant_first", epoch=3),
            json.dumps({"result": response}),
        )


@pytest.mark.parametrize("error", ["endpoint_unavailable", "unsupported_protocol_version"])
async def test_missing_endpoint_is_unsupported_only_for_discovery(error):
    raw = json.dumps({"error": error})
    with pytest.raises(EnvironmentProviderError) as discovery:
        protocol.decode("docker", protocol.Describe(), raw)
    assert discovery.value.code == "desktop_access_unavailable"
    revoke = protocol.Inspect(operation="revoke", generation="a" * 32, id="grant_first", epoch=1)
    with pytest.raises(EnvironmentProviderError) as revocation:
        protocol.decode("docker", revoke, raw)
    assert revocation.value.certainty == EnvironmentProviderOutcomeCertainty.UNKNOWN
    assert revocation.value.code == "desktop_access_unconfirmed"
    active = {
        "id": "grant_first",
        "generation": "a" * 32,
        "epoch": 1,
        "mode": "control",
        "state": "active",
        "remaining_seconds": 10,
    }
    with pytest.raises(EnvironmentProviderError) as unconfirmed:
        protocol.decode("docker", revoke, json.dumps({"result": active}))
    assert unconfirmed.value.certainty == EnvironmentProviderOutcomeCertainty.UNKNOWN


async def test_e2b_access_attaches_only_saved_running_desktop_and_never_starts_it(monkeypatch):
    from a13n_service.providers.environments.e2b import management as module

    management = E2BManagement(
        E2BRecipe(execution="envd_http", desktop=True, template="team-envd"),
        environment_id="env-test",
        state=None,
        runtime=E2BProviderRuntime(SecretStr("test-only")),
        operation_id="op-test",
    )
    opened = AsyncMock()
    monkeypatch.setattr(module, "open_sandbox", opened)
    with pytest.raises(EnvironmentProviderError):
        await management.desktop_access(protocol.Describe())
    management._remember("sandbox-test")
    inspect = AsyncMock(return_value=SimpleNamespace(sandbox_id="sandbox-test", state=SimpleNamespace(value="paused")))
    monkeypatch.setattr(management, "_inspect", inspect)
    with pytest.raises(EnvironmentProviderError):
        await management.desktop_access(protocol.Describe())
    opened.assert_not_awaited()
    inspect.return_value.state.value = "running"
    run = AsyncMock(
        return_value=SimpleNamespace(
            stdout=json.dumps(
                {
                    "result": {
                        "generation": "a" * 32,
                        "protocol_version": 1,
                        "port": 6081,
                        "max_ttl_seconds": 60,
                        "read_only_enforced": True,
                    }
                }
            )
        )
    )
    opened.return_value = SimpleNamespace(commands=SimpleNamespace(run=run))
    closed = AsyncMock()
    monkeypatch.setattr(module, "close_sandbox", closed)
    assert isinstance(await management.desktop_access(protocol.Describe()), protocol.Description)
    assert run.call_args.kwargs["user"] == "root"
    closed.assert_awaited_once_with(opened.return_value)
    run.side_effect = TimeoutError("private transport")
    with pytest.raises(TimeoutError):
        await management.desktop_access(protocol.Describe())
    assert closed.await_count == 2


async def test_revoke_before_delayed_grant_cannot_resurrect_input(supervisor):
    instance, _, commands = supervisor
    result = await request(instance, "revoke", id="control_delayed", epoch=3)
    assert result["state"] == "revoked"
    assert await request(instance, "revoke", id="control_delayed", epoch=3) == result
    with pytest.raises(guest.Refused, match="grant_not_active"):
        await grant(instance, "control_delayed", epoch=3)
    assert commands == []

"""External envd targets against the real `a13n-envd` that `A13N_ENVD_TEST_BINARY` names: registration by endpoint
and token, runs acting in the target, the device identity every connection expects, and endpoint and token changes.

`make eip-integration-test`, which CI runs, builds the daemon and sets the variable; the Service suite leaves this
module out, and without the variable its tests are skipped.
"""

import asyncio
import json
import logging
import os
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_service.runs.environments.adapters import execution_connector

from .environments_support import EXTERNAL_TOKEN as TOKEN
from .environments_support import change, details, register, revealed, stored, target

BINARY = os.environ.get("A13N_ENVD_TEST_BINARY")
# Skip before any fixture builds the service.
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        BINARY is None, reason="set A13N_ENVD_TEST_BINARY to an a13n-envd build to run external targets"
    ),
]


@dataclass
class Daemon:
    device_id: str
    port: int
    workspace: Path
    process: asyncio.subprocess.Process

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class Daemons:
    """Real daemons serving HTTP on loopback with full control of their workspace; all stop with the test."""

    def __init__(self, binary: Path, directory: Path) -> None:
        self.binary, self.directory, self.running = binary, directory, list[Daemon]()

    async def start(self, device_id: str, *, token: str = TOKEN, port: int | None = None) -> Daemon:
        if port is None:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        root = self.directory / f"{device_id}-{port}"
        workspace, runtime = root / "workspace", root / "runtime"
        workspace.mkdir(parents=True)
        runtime.mkdir()
        (root / "token").write_text(token)
        config = root / "envd.json"
        config.write_text(json.dumps({"default_working_directory": str(workspace), "full_control": True}))
        process = await asyncio.create_subprocess_exec(
            str(self.binary),
            "--config",
            str(config),
            env={
                "PATH": os.environ["PATH"],
                "A13N_ENVD_DEVICE_ID": device_id,
                "A13N_ENVD_RUNTIME_DIR": str(runtime),
                "A13N_ENVD_TRANSPORT": "http",
                "A13N_ENVD_HTTP_BIND": f"127.0.0.1:{port}",
                "A13N_ENVD_HTTP_CREDENTIAL_FILE": str(root / "token"),
                "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "loopback",
            },
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        daemon = Daemon(device_id, port, workspace.resolve(), process)
        self.running.append(daemon)
        async with asyncio.timeout(10):
            while True:
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                except OSError:
                    await asyncio.sleep(0.02)
                    continue
                writer.close()
                await writer.wait_closed()
                return daemon

    async def stop(self, daemon: Daemon) -> None:
        if daemon.process.returncode is None:
            daemon.process.terminate()
            await daemon.process.wait()


@pytest.fixture
async def daemons(tmp_path: Path) -> AsyncIterator[Daemons]:
    assert BINARY is not None
    started = Daemons(Path(BINARY), tmp_path / "daemons")
    try:
        yield started
    finally:
        for daemon in started.running:
            await started.stop(daemon)


async def test_a_registered_target_serves_runs_and_keeps_its_token_sealed(  # type: ignore[no-untyped-def]
    service, daemons, scripted_model, runs_kit, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    laptop = await daemons.start("device-laptop")
    registered = await register(service, laptop.endpoint + "/")
    assert registered.status_code == 201, registered.text
    view = registered.json()
    fields = ("provider_id", "template_id", "device_id", "endpoint", "name", "status", "owner_principal_id")
    assert [view[name] for name in fields] == [
        None,
        None,
        "device-laptop",
        laptop.endpoint,
        "device-laptop",
        "ready",
        service.tenant.principal_id,
    ]
    listed = await service.client.get(f"{service.api}/environments")
    assert "token" not in view and TOKEN not in registered.text + listed.text
    row = await stored(service, view["id"])
    assert row.token is not None and TOKEN not in json.dumps(row.token) and revealed(service, row) == TOKEN.encode()

    # A run mounting the target acts in the daemon's workspace.
    agent = await runs_kit.create_agent(service, scripted_model)
    command = "echo external-$((6 * 7)) > proof.txt && cat proof.txt"
    scripted_model.call("shell_exec", {"command": command}, call_id="call_write")
    scripted_model.say("Written.")
    mount = {"name": "workspace", "environment_id": view["id"], "working_directory": str(laptop.workspace)}
    submitted = await runs_kit.start_thread(service, agent, "Write the proof", environments=[mount])
    await (await runs_kit.attempt(service))
    run = await runs_kit.sealed(service, submitted["run"]["id"])
    assert run["status"] == "completed", run["failure"]
    await scripted_model.request()
    answered = await scripted_model.request()
    [result] = [message for message in answered["messages"] if message["role"] == "tool"]
    assert "external-42" in result["content"]
    assert (laptop.workspace / "proof.txt").read_text() == "external-42\n"
    assert TOKEN not in caplog.text


async def test_every_connection_expects_the_registered_device(service, daemons) -> None:  # type: ignore[no-untyped-def]
    laptop = await daemons.start("device-laptop")
    environment_id = (await register(service, laptop.endpoint)).json()["id"]

    # The endpoint now reaches another daemon: the next connection is refused, and so is re-verifying it.
    await daemons.stop(laptop)
    await daemons.start("device-impostor", port=laptop.port)
    adapter = await execution_connector(service.runtime, await target(service, environment_id))
    with pytest.raises(EnvironmentProviderError) as refused:
        await adapter.open()
    # The daemon refuses a connection that expects another device before any session opens.
    assert refused.value.code == "provider_device_mismatch"
    mismatch = await change(service, environment_id, {"token": TOKEN})
    assert mismatch.status_code == 409 and details(mismatch)["reason"] == "provider_device_mismatch", mismatch.text

    # The device moved and its token rotated: a new endpoint comes with its token, stored once they reach it.
    moved = await daemons.start("device-laptop", token="rotated-token")
    alone = await change(service, environment_id, {"endpoint": moved.endpoint})
    assert alone.status_code == 400 and details(alone)["field"] == "token"
    # A daemon refusing the token answers no differently from one that cannot be reached.
    refused_token = await change(service, environment_id, {"endpoint": moved.endpoint, "token": TOKEN})
    assert refused_token.status_code == 503, refused_token.text
    assert details(refused_token)["reason"] == "provider_connection_failed"
    changed = await change(
        service, environment_id, {"endpoint": moved.endpoint, "token": "rotated-token", "name": "Mine"}
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["endpoint"], changed.json()["name"]) == (moved.endpoint, "Mine")
    row = await stored(service, environment_id)
    assert (row.endpoint, row.provider_identity, row.handle) == (moved.endpoint, None, None)
    assert revealed(service, row) == b"rotated-token" and "rotated-token" not in changed.text
    adapter = await execution_connector(service.runtime, await target(service, environment_id))
    async with await adapter.open():
        pass


@pytest.mark.parametrize("lost_cleanup", [False, True])
@pytest.mark.parametrize("successor", [False, True])
async def test_checkpointed_sessions_are_recovered_only_within_the_same_run(
    service, daemons, scripted_model, runs_kit, monkeypatch, lost_cleanup, successor
):
    from a13n_envd_client import EIPSession
    from a13n_service.infra.db import transaction
    from a13n_service.runs import checkpoints
    from a13n_service.runs.seal import LeaseExpirer
    from a13n_service.runs.tables import AttemptRow, RunRow
    from sqlalchemy import update

    await runs_kit.pause_sweeps(service)
    laptop = await daemons.start("device-recovery")
    environment_id = (await register(service, laptop.endpoint)).json()["id"]
    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.call("shell_exec", {"command": "echo first >> proof.txt"}, call_id="call_first")
    first_gate = asyncio.Event()
    scripted_model.say("Interrupted", gate=first_gate)
    submitted = await runs_kit.start_thread(
        service,
        agent,
        "Write proof",
        environments=[
            {"name": "workspace", "environment_id": environment_id, "working_directory": str(laptop.workspace)}
        ],
    )
    run_id = submitted["run"]["id"]

    async def saved_state():
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(RunRow, run_id)
            pointer = checkpoints.StatePointer.model_validate(row.checkpoint)
        result = await checkpoints.load_state(service.runtime.objects, pointer)
        assert result is not None
        return result

    first = await runs_kit.attempt(service)
    await scripted_model.request()
    await scripted_model.request()
    saved = await saved_state()
    assert saved.format == 3
    assert set(saved.envd_sessions) == {"workspace"}
    original = saved.envd_sessions["workspace"].descriptor
    assert TOKEN not in saved.model_dump_json()

    async def lose_remote_cleanup(session):
        # Simulate the remote effect of worker loss while still releasing local
        # test tasks/carriers. Production cleanup remains unchanged.
        session._finish()

    with monkeypatch.context() as patch:
        if lost_cleanup:
            patch.setattr(EIPSession, "close", lose_remote_cleanup)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    first_gate.set()
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(AttemptRow).where(AttemptRow.run_id == run_id).values(lease_expires_at=AttemptRow.created_at)
        )
    await LeaseExpirer(service.runtime, batch=10)()
    async with transaction(service.runtime.storage) as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(available_at=RunRow.created_at))

    if successor:
        # Interrupt keeps the last checkpoint, including hints from the previous
        # attempt. A new user turn inherits history but must not attach its Session.
        response = await service.client.post(f"{service.api}/runs/{run_id}/interrupt")
        assert response.status_code == 200, response.text
        assert (await saved_state()).envd_sessions
        response = await runs_kit.submit(service, submitted["thread"]["id"], runs_kit.message(agent, "Continue"))
        assert response.status_code == 201, response.text
        run_id = response.json()["run"]["id"]

    scripted_model.call("shell_exec", {"command": "echo second >> proof.txt"}, call_id="call_second")
    gate = asyncio.Event()
    scripted_model.say("Recovered", gate=gate)
    second = await runs_kit.attempt(service)
    try:
        await scripted_model.request()
        await scripted_model.request()
        restored = await saved_state()
        current = restored.envd_sessions["workspace"].descriptor
        assert (current.session_id == original.session_id) is (lost_cleanup and not successor)
        assert current.generation == original.generation
        assert (laptop.workspace / "proof.txt").read_text() == "first\nsecond\n"
    finally:
        gate.set()
        await second
    assert (await runs_kit.get_run(service, run_id))["status"] == "completed"
    assert (await saved_state()).envd_sessions == {}

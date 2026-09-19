"""Real envd -> ASGI Control -> Redis -> Worker operation acceptance tests."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import uvicorn
from a13n_harness import EnvironmentAccess
from a13n_harness.providers.environment.commands import ArgvCommand, CommandRequest
from a13n_harness.providers.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy
from a13n_service.environments.websocket.authority import UseIdentity
from a13n_service.environments.websocket.connection_host import ClientConnectionHost
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.environments.websocket.relay_client import RelayUseClient
from a13n_service.environments.websocket.relay_file_operations import RelayFileOperations
from a13n_service.environments.websocket.relay_processes import RelayProcessOperations, RelayShellOperations
from a13n_service.environments.websocket.relay_protocol import RelayEnvironmentSnapshot
from a13n_service.environments.websocket.relay_scope import RelayUseScope
from a13n_service.environments.websocket.relay_storage import ConnectionRelayStore, WorkerResponseMailbox
from a13n_service.environments.websocket.relay_waiters import RelayOperationError, RelayResponseDispatcher
from a13n_service.environments.websocket.resources import ConnectionResources
from a13n_service.environments.websocket.service import ClientConnectionService
from a13n_service.ids import new_object_id
from fastapi import FastAPI, WebSocket
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from ..conftest import actor

pytestmark = pytest.mark.anyio
EXECUTABLE = str(Path(sys.executable).resolve())
POLICY = EnvironmentOutputPolicy(max_inline_bytes=32, max_output_bytes=4096, overflow="retain")


@pytest.fixture
async def host_server(environment_service, target, relay_redis):
    service = ClientConnectionService(
        ConnectionResources(environment_service),
        ConnectionCoordination(relay_redis),
        public_origin="wss://service.example",
    )
    authorized = []

    async def authorize(use, name):
        authorized.append((use, name))
        if name in {None, "workspace", "writer"}:
            return frozenset(EnvironmentAction)
        if name == "reader":
            return EnvironmentAccess("read_only").permission_set().operations
        raise EnvironmentError("No accepted mount", code="environment_forbidden")

    host = ClientConnectionHost(service, relay_redis, authorize)
    app = FastAPI()

    @app.websocket("/api/v1/environments/{environment_id}/connect")
    async def ingress(websocket: WebSocket, environment_id: str):
        await host.serve(websocket, environment_id)

    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off", access_log=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        url = f"ws://127.0.0.1:{listener.getsockname()[1]}/api/v1/environments/{target.environment_id}/connect"
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    assert not serving.done()
                    await asyncio.sleep(0.01)
            yield host, service, url, authorized
        finally:
            await host.close()
            server.should_exit = True
            await asyncio.wait_for(serving, 5)


@asynccontextmanager
async def daemon(binary, directory, url, ticket, native_id, *, expected_exit=0):
    directory.mkdir()
    workspace, runtime = directory / "workspace", directory / "runtime"
    workspace.mkdir()
    runtime.mkdir()
    credential = directory / "credential"
    credential.write_text(ticket)
    credential.chmod(0o600)
    configuration = directory / "envd.json"
    configuration.write_text(
        json.dumps(
            {
                "root_mount_id": "workspace",
                "mounts": [
                    {
                        "mount_id": "workspace",
                        "native_root": str(workspace),
                        "writable": True,
                        "allow_command_execution": True,
                        "max_file_bytes": 8 * 1024 * 1024,
                        "allowed_operations": [
                            "stat",
                            "read_text",
                            "write_text",
                            "open_reader",
                            "open_writer",
                            "list",
                            "find",
                            "search",
                            "command_cwd",
                        ],
                    }
                ],
                "trusted_executable_roots": [str(Path(EXECUTABLE).parent)],
            }
        )
    )
    process = await asyncio.create_subprocess_exec(
        str(binary),
        "--config",
        str(configuration),
        env={
            "A13N_ENVD_ENVIRONMENT_ID": native_id,
            "A13N_ENVD_RUNTIME_DIR": str(runtime),
            "A13N_ENVD_TRANSPORT": "reverse_websocket",
            "A13N_ENVD_EXECUTION_ISOLATION": "disabled",
            "A13N_ENVD_REVERSE_WS_URL": url,
            "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE": str(credential),
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield process, workspace
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
        assert ticket.encode() not in stdout + stderr
        assert process.returncode == expected_exit, stderr.decode(errors="replace")


async def online(service, target, connection_id):
    async with asyncio.timeout(8):
        while True:
            observed = await service.observe(target.organization_id, target.environment_id)
            if (
                observed.value.status == "online"
                and observed.value.connection is not None
                and observed.value.connection.connection_id == connection_id
            ):
                return observed
            await asyncio.sleep(0.02)


async def test_named_mounts_keep_independent_policies_on_one_real_eip_session(
    envd_binary, host_server, target, relay_redis, tmp_path
):
    _, service, url, authorized = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    async with daemon(envd_binary, tmp_path / "aliases", url, ticket.ticket, target.daemon_environment_id) as (_, root):
        observed = await online(service, target, ticket.connection_id)
        identity = UseIdentity(
            observed.value.connection, new_object_id("eu"), "run", "attempt", 1, new_object_id("wrk")
        )
        mailbox = WorkerResponseMailbox(relay_redis, identity.worker_instance_id)
        await mailbox.prepare()
        grant = await service.coordination.acquire_use(identity, attempt_expires_at_ms=observed.value.now_ms + 60_000)
        responses = RelayResponseDispatcher(mailbox)

        scope = RelayUseScope(
            identity,
            grant,
            ConnectionRelayStore(relay_redis, identity.connection),
            responses,
            check_authority=lambda: None,
        )

        def mount(name):
            client = RelayUseClient(scope, mount_name=name)
            client.bind_mount(name)
            return client

        reader, writer, unknown = mount("reader"), mount("writer"), mount("unknown")
        reading = asyncio.create_task(responses.run())
        try:
            with pytest.raises(RelayOperationError) as rejected:
                await reader.call("file.stat", {"path": "/"})
            assert rejected.value.code == "environment_forbidden"
            snapshot = RelayEnvironmentSnapshot.model_validate(await reader.call("scope.describe"))
            assert EnvironmentAction.FILE_WRITE_TEXT not in snapshot.descriptor.permissions.operations
            with pytest.raises(RelayOperationError) as rejected:
                await unknown.call("scope.describe")
            assert rejected.value.code == "environment_forbidden"
            await writer.call("scope.describe")
            await writer.call("file.write_text", {"path": "/shared", "text": "allowed", "mode": "create"})
            assert (await reader.call("file.read_text", {"path": "/shared"}))["text"] == "allowed"
            with pytest.raises(RelayOperationError) as rejected:
                await reader.call("file.write_text", {"path": "/shared", "text": "denied", "mode": "replace"})
            assert rejected.value.code == "environment_forbidden"
            # A caller cannot attach another accepted name to a published handle scope.
            writer.bind_mount("reader")
            with pytest.raises(RelayOperationError) as rejected:
                await writer.call("file.write_text", {"path": "/shared", "text": "denied", "mode": "replace"})
            assert rejected.value.code == "environment_forbidden"
            assert (root / "shared").read_text() == "allowed"
            await reader.call("scope.describe")
            assert authorized == [(identity, None), (identity, "reader"), (identity, "unknown"), (identity, "writer")]
            await reader.call("scope.close")
        finally:
            await scope.invalidate()
            responses.close()
            await reading


async def test_ready_connection_relay_uses_real_envd_and_releases_carrier(
    envd_binary,
    host_server,
    environment_service,
    target,
    relay_redis,
    tmp_path,
):
    host, service, url, authorized = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    async with daemon(envd_binary, tmp_path / "daemon", url, ticket.ticket, target.daemon_environment_id) as (
        _,
        workspace,
    ):
        observed = await online(service, target, ticket.connection_id)
        assert not authorized
        assert (await service.resources.capture(target.organization_id, target.environment_id)).status == "running"
        assert environment_service.sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        identity = UseIdentity(
            observed.value.connection, new_object_id("eu"), "run", "attempt", 1, new_object_id("wrk")
        )
        mailbox = WorkerResponseMailbox(relay_redis, identity.worker_instance_id)
        await mailbox.prepare()
        grant = await service.coordination.acquire_use(identity, attempt_expires_at_ms=observed.value.now_ms + 60_000)
        responses = RelayResponseDispatcher(mailbox)
        scope = RelayUseScope(
            identity,
            grant,
            ConnectionRelayStore(relay_redis, identity.connection),
            responses,
            check_authority=lambda: None,
        )
        client = RelayUseClient(scope)
        reader = asyncio.create_task(responses.run())
        try:
            snapshot = RelayEnvironmentSnapshot.model_validate(await client.call("scope.describe"))
            assert "files" in snapshot.descriptor.operation_families
            client.bind_mount("mount-worker")
            files = RelayFileOperations(client)
            content = bytes(range(256)) * 5000

            async def source():
                yield content

            result = await files.write_bytes_stream("/bytes", source(), mode="create")
            assert result.bytes_written == len(content)
            assert await files.read_bytes("/bytes") == content
            assert (workspace / "bytes").read_bytes() == content
            try:
                shell = await RelayShellOperations(client).exec(
                    CommandRequest(
                        command=ArgvCommand(executable=Path(EXECUTABLE).name, arguments=("-c", "print('relay')")),
                        output_policy=POLICY,
                    )
                )
            except RelayOperationError as error:
                pytest.fail(str(error.failure.model_dump()))
            assert shell.output.stdout.inline == b"relay\n"
            started = await RelayProcessOperations(client).start(
                CommandRequest(
                    command=ArgvCommand(executable=Path(EXECUTABLE).name, arguments=("-c", "pass")),
                    output_policy=POLICY,
                )
            )
            assert started.process.handle.mount_id == "mount-worker"
            client.bind_mount("mount-other")
            with pytest.raises(RelayOperationError):
                await RelayProcessOperations(client).inspect(started.process.handle)
            assert authorized == [(identity, None), (identity, "workspace")]
            await client.call("scope.close")
        finally:
            await scope.invalidate()
            responses.close()
            await reader
        async with asyncio.timeout(3):
            while (await service.observe(target.organization_id, target.environment_id)).value.status != "offline":
                await asyncio.sleep(0.01)
        current = await service.resources.capture(target.organization_id, target.environment_id)
        assert current.generation == target.generation
        async with asyncio.timeout(3):
            while host._active:
                await asyncio.sleep(0.01)


async def test_takeover_replaces_ready_connection_without_changing_backing_generation(
    envd_binary, host_server, target, tmp_path
):
    _, service, url, _ = host_server
    first = await service.issue_ticket(actor(), target.environment_id)
    async with daemon(
        envd_binary, tmp_path / "first", url, first.ticket, target.daemon_environment_id, expected_exit=1
    ) as (retired, _):
        old = await online(service, target, first.connection_id)
        second = await service.issue_ticket(actor(), target.environment_id)
        async with daemon(envd_binary, tmp_path / "second", url, second.ticket, target.daemon_environment_id):
            new = await online(service, target, second.connection_id)
            assert old.value.connection != new.value.connection
            assert (
                await service.resources.capture(target.organization_id, target.environment_id)
            ).generation == target.generation
            assert new.value.retiring is None
            assert await asyncio.wait_for(retired.wait(), 5) == 1


async def test_ticket_cannot_be_replayed_or_supplied_in_query(host_server, target):
    host, service, url, _ = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    with pytest.raises(InvalidStatus):
        async with connect(url + "?ticket=" + ticket.ticket, subprotocols=["eip.v1"]):
            pytest.fail("query credential was accepted")
    async with connect(url, additional_headers={"Authorization": "Bearer " + ticket.ticket}, subprotocols=["eip.v1"]):
        with pytest.raises(InvalidStatus):
            async with connect(
                url, additional_headers={"Authorization": "Bearer " + ticket.ticket}, subprotocols=["eip.v1"]
            ):
                pytest.fail("one-use credential was replayed")
    await host.close()


async def test_lost_database_commit_reply_reuses_publication_without_reinitializing_sdk(
    envd_binary,
    host_server,
    target,
    tmp_path,
    monkeypatch,
):
    from a13n_envd_client import EIPSession
    from sqlalchemy.exc import DBAPIError

    _, service, url, _ = host_server
    original_publish = service.resources.publish
    original_initialize = EIPSession.initialize
    publications, initializations = [], []

    async def publish(target, status, **kwargs):
        result = await original_publish(target, status, **kwargs)
        if status == "running":
            publications.append((target, kwargs["publication_id"]))
            if len(publications) == 1:
                raise DBAPIError("", {}, OSError("lost commit reply"), connection_invalidated=True)
        return result

    async def initialize(*args, **kwargs):
        initializations.append(True)
        return await original_initialize(*args, **kwargs)

    monkeypatch.setattr(service.resources, "publish", publish)
    monkeypatch.setattr(EIPSession, "initialize", initialize)
    ticket = await service.issue_ticket(actor(), target.environment_id)
    async with daemon(envd_binary, tmp_path / "daemon", url, ticket.ticket, target.daemon_environment_id):
        await online(service, target, ticket.connection_id)
        assert len(initializations) == 1
        assert len(publications) == 2 and publications[0] == publications[1]


async def test_wrong_native_identity_never_becomes_online(envd_binary, host_server, target, tmp_path):
    _, service, url, authorized = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    async with daemon(envd_binary, tmp_path / "wrong", url, ticket.ticket, "wrong-native", expected_exit=1) as (
        process,
        _,
    ):
        assert await asyncio.wait_for(process.wait(), 8) == 1
        async with asyncio.timeout(3):
            while (await service.observe(target.organization_id, target.environment_id)).value.status != "offline":
                await asyncio.sleep(0.01)
        status = await service.status(actor(), target.environment_id)
        assert status.error == "environment_initialization_failed"
        assert not authorized
        current = await service.resources.capture(target.organization_id, target.environment_id)
        assert current.status != "running" and current.generation == target.generation


async def test_drain_closes_candidate_and_rejects_new_admission(host_server, target):
    host, service, url, authorized = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    async with connect(
        url, additional_headers={"Authorization": "Bearer " + ticket.ticket}, subprotocols=["eip.v1"]
    ) as socket:
        await host.close()
        await asyncio.wait_for(socket.wait_closed(), 2)
    assert not host._active and not authorized
    assert (await service.status(actor(), target.environment_id)).status == "offline"
    fresh = await service.issue_ticket(actor(), target.environment_id)
    with pytest.raises(InvalidStatus):
        async with connect(
            url, additional_headers={"Authorization": "Bearer " + fresh.ticket}, subprotocols=["eip.v1"]
        ):
            pytest.fail("draining owner accepted another carrier")

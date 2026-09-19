"""Real envd -> ASGI Control -> Redis -> Worker operation acceptance tests."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn
from a13n_environment import EnvironmentAction, EnvironmentError
from a13n_environment.commands import ArgvCommand, CommandRequest
from a13n_environment.retention import EnvironmentOutputPolicy
from a13n_service.environments.websocket.authority import UseIdentity
from a13n_service.environments.websocket.connection_host import ClientConnectionHost
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.environments.websocket.relay_client import RelayUseClient
from a13n_service.environments.websocket.relay_file_operations import RelayFileOperations
from a13n_service.environments.websocket.relay_processes import RelayProcessOperations, RelayShellOperations
from a13n_service.environments.websocket.relay_protocol import RelayEnvironmentSnapshot
from a13n_service.environments.websocket.relay_runtime import RelayResponseRuntime
from a13n_service.environments.websocket.relay_scope import RelayUseScope
from a13n_service.environments.websocket.relay_storage import ConnectionRelayStore, ResponseMailbox
from a13n_service.environments.websocket.relay_waiters import RelayOperationError, RelayResponseDispatcher
from a13n_service.environments.websocket.resources import ConnectionResources
from a13n_service.environments.websocket.service import ClientConnectionService
from a13n_service.environments.websocket.transport import ClientWebSocket
from a13n_service.environments.websocket.worker_connections import WorkerClientConnections
from a13n_service.ids import new_object_id
from fastapi import FastAPI, WebSocket
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from ..conftest import actor
from .test_worker_connections import attempt as attempt

pytestmark = pytest.mark.anyio
EXECUTABLE = str(Path(sys.executable).resolve())
POLICY = EnvironmentOutputPolicy(max_inline_bytes=32, max_output_bytes=4096, overflow="retain")


@pytest.fixture
def binding_directories():
    return {}


@pytest.fixture
async def host_server(environment_service, target, relay_redis, binding_directories):
    service = ClientConnectionService(
        ConnectionResources(environment_service),
        ConnectionCoordination(relay_redis),
        public_origin="wss://service.example",
    )
    authorized = []

    async def authorize(use):
        authorized.append(use)
        if use.mount_name not in binding_directories:
            raise EnvironmentError("No accepted mount", code="environment_forbidden")
        return binding_directories[use.mount_name]

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
async def daemon(binary, directory, url, ticket, native_id, *, expected_exit=0, max_sessions=128):
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
                "device_id": native_id,
                "default_working_directory": str(workspace),
                "limits": {"max_sessions": max_sessions},
                "trusted_executable_roots": [str(Path(EXECUTABLE).parent)],
            }
        )
    )
    process = await asyncio.create_subprocess_exec(
        str(binary),
        "--config",
        str(configuration),
        env={
            "A13N_ENVD_RUNTIME_DIR": str(runtime),
            "A13N_ENVD_TRANSPORT": "reverse_websocket",
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


@asynccontextmanager
async def relay_clients(service, observed, redis, names):
    worker = new_object_id("wrk")
    mailbox = ResponseMailbox(redis, worker)
    await mailbox.prepare()
    responses = RelayResponseDispatcher(mailbox)
    scopes, clients = [], {}
    for name in names:
        identity = UseIdentity(
            observed.value.connection,
            new_object_id("eu"),
            "run",
            "attempt",
            1,
            worker,
            name,
            admission_deadline_ms=observed.value.expires_at_ms,
        )
        grant = await service.coordination.acquire_use(identity, attempt_expires_at_ms=observed.value.now_ms + 60_000)
        scope = RelayUseScope(
            identity, grant, ConnectionRelayStore(redis, identity.connection), responses, check_authority=lambda: None
        )
        scopes.append(scope)
        clients[name] = RelayUseClient(scope, mount_name=name)

    async def renew():
        while True:
            await asyncio.sleep(0.3)
            for scope in scopes:
                if not scope.available:
                    continue
                from a13n_service.environments.websocket.coordination import CoordinationError

                try:
                    await scope.renew(
                        await service.coordination.renew_use(
                            scope.identity, attempt_expires_at_ms=observed.value.now_ms + 60_000
                        )
                    )
                except CoordinationError:
                    await scope.invalidate()

    reading, renewing = asyncio.create_task(responses.run()), asyncio.create_task(renew())
    try:
        yield clients
    finally:
        renewing.cancel()
        await asyncio.gather(renewing, return_exceptions=True)
        for scope in scopes:
            await scope.invalidate()
        responses.close()
        await reading


async def test_named_bindings_own_distinct_sessions_directories_and_capabilities(
    envd_binary, host_server, target, relay_redis, tmp_path, binding_directories
):
    _, service, url, authorized = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    async with daemon(envd_binary, tmp_path / "aliases", url, ticket.ticket, target.device_id) as (_, root):
        data = root / "data"
        data.mkdir()
        binding_directories.update(reader=str(data), writer=str(root))
        observed = await online(service, target, ticket.connection_id)
        async with relay_clients(service, observed, relay_redis, ("reader", "writer", "unknown")) as clients:
            reader, writer, unknown = (clients[name] for name in ("reader", "writer", "unknown"))
            with pytest.raises(RelayOperationError) as rejected:
                await reader.call("file.stat", {"path": str(root)})
            assert rejected.value.code == "environment_unavailable"
            snapshot = RelayEnvironmentSnapshot.model_validate(await reader.call("scope.describe"))
            writer_snapshot = RelayEnvironmentSnapshot.model_validate(await writer.call("scope.describe"))
            assert snapshot.descriptor.working_directory == str(data)
            assert writer_snapshot.descriptor.working_directory == str(root)
            assert snapshot.descriptor.generation != writer_snapshot.descriptor.generation
            assert EnvironmentAction.FILE_WRITE_TEXT in snapshot.descriptor.permissions.operations
            with pytest.raises(RelayOperationError) as rejected:
                await unknown.call("scope.describe")
            assert rejected.value.code == "environment_forbidden"
            shared = str(root / "shared")
            await writer.call("file.write_text", {"path": shared, "text": "allowed", "mode": "create"})
            # The fixed cwd is not an access root.
            assert (await reader.call("file.read_text", {"path": shared}))["text"] == "allowed"
            await reader.call("file.write_text", {"path": shared, "text": "updated", "mode": "replace"})
            await reader.call("scope.close")
            assert (await writer.call("file.read_text", {"path": shared}))["text"] == "updated"
            assert (await service.observe(target.organization_id, target.environment_id)).value.status == "online"
            assert [use.mount_name for use in authorized] == ["reader", "writer", "unknown"]
            await writer.call("scope.close")
            assert (await service.observe(target.organization_id, target.environment_id)).value.status == "online"


async def test_ready_connection_relay_streams_and_closes_only_its_session(
    envd_binary,
    host_server,
    environment_service,
    target,
    relay_redis,
    tmp_path,
    binding_directories,
):
    host, service, url, authorized = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    async with daemon(envd_binary, tmp_path / "daemon", url, ticket.ticket, target.device_id) as (
        _,
        workspace,
    ):
        observed = await online(service, target, ticket.connection_id)
        assert not authorized
        assert (await service.resources.capture(target.organization_id, target.environment_id)).status == "running"
        assert environment_service.sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        binding_directories["workspace"] = str(workspace)
        async with relay_clients(service, observed, relay_redis, ("workspace",)) as clients:
            client = clients["workspace"]
            snapshot = RelayEnvironmentSnapshot.model_validate(await client.call("scope.describe"))
            assert "files" in snapshot.descriptor.operation_families
            client.bind_mount("mount-worker")
            files = RelayFileOperations(client)
            content = bytes(range(256)) * 5000

            async def source():
                yield content

            result = await files.write_bytes_stream(str(workspace / "bytes"), source(), mode="create")
            assert result.bytes_written == len(content)
            try:
                downloaded = await files.read_bytes(str(workspace / "bytes"))
            except RelayOperationError as error:
                pytest.fail(str(error.failure.model_dump()))
            assert downloaded == content
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
            assert authorized == [client.identity]
            await client.call("scope.close")
        assert (await service.observe(target.organization_id, target.environment_id)).value.status == "online"
        current = await service.resources.capture(target.organization_id, target.environment_id)
        assert current.generation == target.generation
        assert host._active


async def test_takeover_replaces_ready_connection_without_changing_backing_generation(
    envd_binary, host_server, target, tmp_path
):
    _, service, url, _ = host_server
    first = await service.issue_ticket(actor(), target.environment_id)
    async with daemon(envd_binary, tmp_path / "first", url, first.ticket, target.device_id, expected_exit=1) as (
        retired,
        _,
    ):
        old = await online(service, target, first.connection_id)
        second = await service.issue_ticket(actor(), target.environment_id)
        async with daemon(envd_binary, tmp_path / "second", url, second.ticket, target.device_id):
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
    from a13n_envd_client import EIPDeviceConnection
    from sqlalchemy.exc import DBAPIError

    _, service, url, _ = host_server
    original_publish = service.resources.publish
    original_initialize = EIPDeviceConnection.initialize
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
    monkeypatch.setattr(EIPDeviceConnection, "initialize", initialize)
    ticket = await service.issue_ticket(actor(), target.environment_id)
    async with daemon(envd_binary, tmp_path / "daemon", url, ticket.ticket, target.device_id):
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


@pytest.mark.parametrize("retirement", ["release", "revocation"])
async def test_retired_sessions_release_native_capacity_without_disturbing_sibling(
    envd_binary, host_server, target, relay_redis, tmp_path, binding_directories, attempt, monkeypatch, retirement
):
    _, service, url, _ = host_server
    ticket = await service.issue_ticket(actor(), target.environment_id)
    attempt = replace(attempt, organization_id=target.organization_id)
    responses = RelayResponseRuntime(relay_redis, relay_redis, attempt.worker_id)
    await responses.prepare()
    connections = WorkerClientConnections(relay_redis, responses)
    reading, renewing = asyncio.create_task(responses.run()), asyncio.create_task(connections.run())
    closes = []
    send = ClientWebSocket._send

    async def record_close(carrier, message):
        await send(carrier, message)
        if isinstance(message, str) and json.loads(message).get("method") == "session.close":
            closes.append(json.loads(message)["eip_session"])

    monkeypatch.setattr(ClientWebSocket, "_send", record_close)
    try:
        async with daemon(envd_binary, tmp_path / "churn", url, ticket.ticket, target.device_id, max_sessions=2) as (
            _,
            root,
        ):
            binding_directories.update(workspace=str(root), reader=str(root))
            await online(service, target, ticket.connection_id)
            sibling = await connections.acquire(attempt, target.environment_id, mount_name="reader")
            await sibling.call("scope.describe")
            for cycle in range(6):
                next_attempt = replace(attempt, run_id=f"run-{cycle}", run_attempt_id=f"attempt-{cycle}")
                client = await connections.acquire(next_attempt, target.environment_id)
                await client.call("scope.describe")
                if retirement == "release":
                    await connections.release(client)
                else:
                    await service.coordination.release_use(client.identity)
                    async with asyncio.timeout(3):
                        while client.available or len(closes) <= cycle:
                            await asyncio.sleep(0.01)
                    await connections.release(client)
                assert len(closes) == cycle + 1, closes
                await sibling.call("file.stat", {"path": str(root)})
                assert (await service.observe(target.organization_id, target.environment_id)).value.status == "online"
            await connections.release(sibling)
            assert len(closes) == 7
    finally:
        await connections.close()
        await renewing
        await responses.close()
        await reading

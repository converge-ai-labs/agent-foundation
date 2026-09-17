"""Real Attempt authority -> Worker adapter -> Control -> native envd."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest
import uvicorn
from a13n_environment import EnvironmentAction, EnvironmentError
from a13n_environment.commands import ArgvCommand, CommandRequest
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.mount_domain import AddEnvironmentMountRequest
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.environments.mount_observations import RunMountObservations
from a13n_service.environments.mounts import RunEnvironmentMountService
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.environments.websocket.admission import OnlineAdmission
from a13n_service.environments.websocket.connection_host import ClientConnectionHost
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.environments.websocket.resources import ConnectionResources
from a13n_service.environments.websocket.service import ClientConnectionService
from a13n_service.environments.websocket.use_authorization import ClientUseAuthorization
from a13n_service.environments.websocket.worker_connections import WorkerClientConnections
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session
from a13n_service.storage.config import RedisServerConfig
from a13n_service.storage.redis import open_redis
from fastapi import FastAPI, WebSocket
from sqlalchemy import event

from tests.environments.websocket.conftest import envd_binary as envd_binary
from tests.environments.websocket.conftest import relay_redis as relay_redis
from tests.environments.websocket.test_connection_host import EXECUTABLE, POLICY, daemon, online
from tests.hooks.support import hook_actor

from .test_attempt_execution import _authority
from .test_websocket_use_authorization import admitted_use as admitted_use
from .test_websocket_use_authorization import client_environment as client_environment
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


@pytest.fixture
async def native_client(client_environment, interaction_sessions, relay_redis, envd_binary, tmp_path):
    environment_service, _, environment = client_environment
    service = ClientConnectionService(
        ConnectionResources(environment_service),
        ConnectionCoordination(relay_redis),
        public_origin="wss://service.example",
    )
    host = ClientConnectionHost(service, relay_redis, ClientUseAuthorization(interaction_sessions))
    target = await service.resources.authorized(hook_actor(), environment.id)
    ticket = await service.issue_ticket(hook_actor(), environment.id)
    app = FastAPI()

    @app.websocket("/connect")
    async def ingress(websocket: WebSocket):
        await host.serve(websocket, environment.id)

    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off", access_log=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        url = f"ws://127.0.0.1:{listener.getsockname()[1]}/connect"
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    assert not serving.done()
                    await asyncio.sleep(0.01)
            async with daemon(envd_binary, tmp_path / "client", url, ticket.ticket, "native", expected_exit=1) as (
                process,
                workspace,
            ):
                try:
                    await online(service, target, ticket.connection_id)
                    yield service, target, workspace
                finally:
                    await host.close()
                    await asyncio.wait_for(process.wait(), 5)
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, 5)


@pytest.fixture
async def client_runtime(admitted_use, native_client, interaction_sessions, relay_redis, redis_url, tmp_path):
    case = admitted_use
    service, target, workspace = native_client
    attempt = await prepare_permissions(interaction_sessions, case.run, _authority(case.claim))
    lifecycle = EnvironmentLifecycle(
        interaction_sessions, case.service.catalog, case.service.protector, tmp_path / "service-files"
    )
    async with open_redis(RedisServerConfig(url=redis_url, max_connections=1)) as reader:
        connections = WorkerClientConnections(relay_redis, reader, attempt.worker_id)
        await connections.prepare()
        receiving = asyncio.create_task(connections.run())
        try:
            yield lifecycle, connections, attempt, workspace, service, target
        finally:
            await connections.close()
            await receiving


@pytest.mark.parametrize("admitted_use", ["full"], indirect=True)
async def test_worker_facets_renew_without_database_io_and_fence_lost_attempt(client_runtime, interaction_sessions):
    lifecycle, connections, attempt, workspace, service, target = client_runtime
    environment = await prepare_run_environment(lifecycle, attempt, client_connections=connections)
    assert environment is not None
    await environment.enter(
        thread_id=attempt.thread_id, run_id=attempt.run_id, agent_instance_id="agent", mount_id="mount-harness"
    )
    await environment.ensure_ready(frozenset({"files", "shell"}))
    statements = []

    def statement(*args):
        statements.append(args[2])

    engine = interaction_sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", statement)
    try:
        files, shell = environment.operations.files, environment.operations.shell
        assert files is not None and shell is not None
        await files.write_text("/worker.txt", "from Worker", mode="create")
        assert (await files.read_text("/worker.txt")).text == "from Worker"
        operation = asyncio.create_task(
            shell.exec(
                CommandRequest(
                    command=ArgvCommand(
                        executable=Path(EXECUTABLE).name,
                        arguments=("-c", "import time; time.sleep(2.5); print('renewed')"),
                    ),
                    output_policy=POLICY,
                )
            )
        )
        await asyncio.sleep(0.2)
        assert engine.pool.checkedout() == 0
        assert (await operation).output.stdout.inline == b"renewed\n"
        assert not statements
        assert environment.availability.status == "available"
        assert environment.backing_generation == target.generation
        attempt.lease.invalidate()
        assert environment.availability.status == "unavailable"
        with pytest.raises(EnvironmentError) as denied:
            await files.write_text("/after-fence.txt", "forbidden", mode="create")
        assert denied.value.code == "environment_unavailable"
        assert not (workspace / "after-fence.txt").exists()
    finally:
        event.remove(engine, "before_cursor_execute", statement)
        await environment.close()
    async with short_session(interaction_sessions) as session:
        run = await session.get(RunRecord, attempt.run_id)
        resource = await session.get(EnvironmentRecord, target.environment_id)
        assert run.environment_use_started_at is not None
        assert resource.generation == target.generation
        assert resource.operation_id is None
    async with asyncio.timeout(3):
        while (await service.observe(target.organization_id, target.environment_id)).value.status != "offline":
            await asyncio.sleep(0.01)


async def test_read_only_use_rejects_write_before_redis_publication(client_runtime, monkeypatch):
    lifecycle, connections, attempt, workspace, _, _ = client_runtime
    (workspace / "existing.txt").write_text("readable")
    environment = await prepare_run_environment(lifecycle, attempt, client_connections=connections)
    assert environment is not None
    await environment.enter(
        thread_id=attempt.thread_id, run_id=attempt.run_id, agent_instance_id="agent", mount_id="mount-readonly"
    )
    await environment.ensure_ready(frozenset({"files"}))
    files = environment.operations.files
    assert files is not None
    assert EnvironmentAction.FILE_WRITE_TEXT not in environment.descriptor.permissions.operations
    assert environment.operations.shell is None
    assert (await files.read_text("/existing.txt")).text == "readable"

    async def unexpected(*args, **kwargs):
        pytest.fail("forbidden request reached Redis")

    monkeypatch.setattr(environment._client._scope.store, "append", unexpected)
    with pytest.raises(EnvironmentError) as denied:
        await files.write_text("/forbidden.txt", "no", mode="create")
    assert denied.value.code == "environment_forbidden"
    assert not (workspace / "forbidden.txt").exists()
    monkeypatch.undo()
    await environment.close()


@pytest.mark.parametrize("admitted_use", [None, "read_only"], indirect=True)
async def test_accepted_addition_prepares_with_its_own_access_and_retained_use(client_runtime, interaction_sessions):
    lifecycle, connections, attempt, workspace, service, target = client_runtime
    primary = await prepare_run_environment(lifecycle, attempt, client_connections=connections)
    if primary is not None:
        await primary.enter(
            thread_id=attempt.thread_id, run_id=attempt.run_id, agent_instance_id="agent", mount_id="primary-harness"
        )
    mounts = RunEnvironmentMountService(
        interaction_sessions, OnlineAdmission(interaction_sessions, service.coordination)
    )
    await mounts.add(
        actor=hook_actor(),
        run_id=attempt.run_id,
        idempotency_key="live-writer",
        request=AddEnvironmentMountRequest(name="writer", environment_id=target.environment_id, access="full"),
    )
    mount = (await RunMountObservations(interaction_sessions).snapshot(attempt))[0]
    addition = await prepare_run_environment(lifecycle, attempt, mount=mount, client_connections=connections)
    assert addition is not None and addition.access == "full"
    await addition.enter(
        thread_id=attempt.thread_id, run_id=attempt.run_id, agent_instance_id="agent", mount_id="writer-harness"
    )
    await addition.ensure_ready(frozenset({"files"}))
    files = addition.operations.files
    assert files is not None
    await files.write_text("/from-addition.txt", "shared target", mode="create")
    assert (workspace / "from-addition.txt").read_text() == "shared target"
    if primary is not None:
        assert primary._client.identity == addition._client.identity
        assert EnvironmentAction.FILE_WRITE_TEXT not in primary.descriptor.permissions.operations
        with pytest.raises(EnvironmentError) as denied:
            await primary.operations.files.write_text("/from-addition.txt", "forbidden", mode="replace")
        assert denied.value.code == "environment_forbidden"
        assert (workspace / "from-addition.txt").read_text() == "shared target"
    await addition.close()
    if primary is not None:
        await primary.ensure_ready(frozenset({"files"}))
        assert (await primary.operations.files.read_text("/from-addition.txt")).text == "shared target"
        await primary.close()
    async with short_session(interaction_sessions) as session:
        run = await session.get(RunRecord, attempt.run_id)
        row = await session.get(RunEnvironmentMountRecord, (attempt.run_id, "writer"))
        assert row.use_started_at is not None
        assert (run.environment_use_started_at is not None) == (primary is not None)
        assert (run.environment_id is not None) == (primary is not None)

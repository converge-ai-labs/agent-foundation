from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

import pytest
from a13n_environment import EnvironmentError, build_environment_provider_catalog
from a13n_service.environments.domain import CreateProviderRequest, RegisterEnvironmentRequest
from a13n_service.environments.websocket.authority import ConnectionIdentity, DispatchAuthority, UseIdentity
from a13n_service.environments.websocket.coordination import ConfirmedObservation, ConnectionObservation, UseGrant
from a13n_service.environments.websocket.relay_client import RelayUseClient
from a13n_service.environments.websocket.relay_consumer import RelayControlConsumer
from a13n_service.environments.websocket.relay_scope import RelayUseScope
from a13n_service.environments.websocket.relay_storage import ConnectionRelayStore, ResponseMailbox
from a13n_service.environments.websocket.relay_waiters import RelayResponseDispatcher
from a13n_service.environments.websocket.resources import ConnectionResources
from a13n_service.storage.config import RedisServerConfig
from a13n_service.storage.redis import open_redis

from ..conftest import WORKSPACE_ID, actor


@pytest.fixture
def envd_binary():
    value = os.environ.get("A13N_ENVD_TEST_BINARY")
    if value is None:
        pytest.skip("set A13N_ENVD_TEST_BINARY to exercise reverse WebSocket Control with real envd")
    binary = Path(value).resolve()
    assert binary.is_file()
    return binary


@pytest.fixture
def provider_catalog():
    return build_environment_provider_catalog(builtin_keys=("a13n.websocket-envd",))


@pytest.fixture
async def target(environment_service):
    provider = await environment_service.create_provider(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(type="a13n.websocket-envd", name="Client"),
    )
    environment = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="register-client",
        request=RegisterEnvironmentRequest(
            provider_id=provider.id,
            configuration={},
            device_id="local-computer",
        ),
    )
    return await ConnectionResources(environment_service).authorized(actor(), environment.id, manage=True)


@pytest.fixture
async def relay_redis(redis_url):
    # The memory backend does not implement Stream subcommands within Lua.
    # Its explicit startup rejection is covered by the capability-probe test.
    async with open_redis(RedisServerConfig(url=redis_url)) as client:
        await client.flushdb()
        try:
            yield client
        finally:
            await client.flushdb()


CONNECTION = ConnectionIdentity("org", "env", "connection", "epoch", "control")
USE = UseIdentity(CONNECTION, "use", "run", "attempt", 1, "worker", "workspace", admission_deadline_ms=1)


@pytest.fixture
async def control_relay(relay_redis):
    owner = ConnectionRelayStore(relay_redis, CONNECTION)
    mailbox = ResponseMailbox(relay_redis, USE.worker_instance_id)
    await mailbox.prepare()
    await owner.prepare()
    started = monotonic()
    seconds, micros = await relay_redis.time()
    now = seconds * 1000 + micros // 1000
    observed = ConfirmedObservation(
        ConnectionObservation(
            code="ok",
            now_ms=now,
            status="online",
            connection=CONNECTION,
            expires_at_ms=now + 5000,
            barrier_ms=0,
            retiring=None,
            uses={USE.use_id: UseGrant(identity=USE, expires_at_ms=now + 5000)},
            error=None,
        ),
        started,
        0.005,
    )
    responses = RelayResponseDispatcher(mailbox)
    scope = RelayUseScope(USE, observed, owner, responses, check_authority=lambda: None)
    client = RelayUseClient(scope)
    reader = asyncio.create_task(responses.run())

    @asynccontextmanager
    async def serving(dispatch, *, concurrency=32):
        authority = DispatchAuthority(CONNECTION, observed.deadline())

        @asynccontextmanager
        async def operation(request):
            if request.scope != USE:
                raise EnvironmentError("The test binding is not admitted", code="environment_forbidden")
            if request.operation in {"scope.close", "operation.cancel"}:

                async def noop():
                    return None

                yield noop
            else:
                yield dispatch.prepare(request)

        consumer = RelayControlConsumer(
            owner,
            authority,
            observed,
            operation,
            concurrency=concurrency,
        )
        task = asyncio.create_task(consumer.run())
        try:
            yield client, owner, consumer, task
        finally:
            await consumer.close()
            await task

    try:
        yield serving
    finally:
        await scope.invalidate()
        responses.close()
        await reader

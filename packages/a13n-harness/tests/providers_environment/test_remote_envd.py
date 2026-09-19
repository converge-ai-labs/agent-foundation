"""Remote Provider codecs and Host-owned WebSocket lifecycle, without network I/O."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.models import EnvironmentState
from a13n_harness.providers.environment.remote_envd import connections as module
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    RemoteEnvdEnvironmentConfiguration,
)
from a13n_harness.providers.environment.remote_envd.connections import WebSocketEnvdConnections
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD, HttpEnvdProviderRuntime
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD, WebSocketEnvdProviderRuntime
from pydantic import SecretStr, ValidationError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def state(key="http_envd", native="env-native"):
    return EnvironmentState(provider_key=key, state_version="1", state={"daemon_environment_id": native})


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://remote.example",
        "https://user:secret@example.com",
        "https://example.com/?token=x",
        "https://example.com/path",
        "https://example.com:0",
        "https://example.com:invalid",
        " https://example.com",
    ],
)
def test_http_rejects_unsafe_or_invalid_backend(endpoint):
    with pytest.raises(ValidationError):
        HttpEnvdConnectionConfiguration(endpoint=endpoint)


async def test_remote_catalog_codecs_are_inert_and_external_only(tmp_path):
    catalog = ProviderCatalog(select_builtin_environment_providers(("http_envd", "websocket_envd")))
    hub = WebSocketEnvdConnections()
    runtimes = (
        HttpEnvdProviderRuntime(
            HttpEnvdConnectionConfiguration(endpoint="https://example.com/"),
            HttpEnvdCredential(token=SecretStr("secret")),
        ),
        WebSocketEnvdProviderRuntime(hub),
    )
    for provider, runtime in zip(catalog.values(), runtimes, strict=True):
        recipe = provider.validate_environment({})
        assert not provider.supports_managed
        assert not provider.supports_stop and not provider.supports_destroy and not provider.requires_keepalive
        assert provider.target_identity(configuration=recipe, state=state(provider.type)) == "env-native"
        environment = provider.construct(
            configuration=recipe, environment_id="env-logical", state=state(provider.type), runtime=runtime
        )
        await environment.enter(mount_id="m")
        assert environment.environment_id == "env-logical"
        assert environment.descriptor.generation == "unprepared"
        assert environment.operations.files is None
        assert environment.dump_state() == state(provider.type)
        await environment.close()
        assert environment.dump_state() == state(provider.type)
        with pytest.raises(EnvironmentProviderError):
            provider.construct(configuration=recipe, environment_id="env-logical", state=None, runtime=runtime)
        for invalid in (
            state("wrong_provider"),
            EnvironmentState(provider_key=provider.type, state_version="2", state={}),
            EnvironmentState(
                provider_key=provider.type,
                state_version="1",
                state={"daemon_environment_id": "env-native", "credential": "secret"},
            ),
        ):
            with pytest.raises(EnvironmentProviderError):
                provider.target_identity(configuration=recipe, state=invalid)
        with pytest.raises(EnvironmentProviderError):
            provider.validate_environment({"endpoint": "not-a-recipe"})
    await hub.close()


async def test_lifecycle_never_prepares_or_destroys_external_daemon():
    provider = HTTP_ENVD
    runtime = HttpEnvdProviderRuntime(
        HttpEnvdConnectionConfiguration(endpoint="https://example.com"), HttpEnvdCredential(token=SecretStr("secret"))
    )
    for action in ("stop", "destroy", "reconcile"):
        env = provider.construct(
            configuration=RemoteEnvdEnvironmentConfiguration(),
            environment_id="env-logical",
            state=state(),
            runtime=runtime,
        )
        with pytest.raises(EnvironmentProviderError) as failure:
            if action == "stop":
                await env.stop()
            elif action == "destroy":
                await env.destroy()
            else:
                await env.reconcile()
        assert failure.value.code == "provider_operation_unsupported"
        assert env.dump_state() == state()
        await env.close()


async def test_host_runtime_injection_and_managed_rejection():
    from a13n_harness.providers.environment.remote_envd.websocket import WebSocketEnvdProviderRuntime

    provider = WEBSOCKET_ENVD
    with pytest.raises(EnvironmentProviderError) as error:
        await provider.create({}, state=state("websocket_envd"), allow_create=False)
    assert error.value.code == "provider_runtime_required"
    async with WebSocketEnvdConnections() as hub:
        runtime = WebSocketEnvdProviderRuntime(hub)
        environment = await provider.create({}, state=state("websocket_envd"), allow_create=False, runtime=runtime)
        assert environment.provider_key == "websocket_envd"
        await environment.close()
        with pytest.raises(EnvironmentProviderError) as error:
            await provider.create({}, state=state("websocket_envd"), runtime=runtime)
        assert error.value.code == "provider_external_only"


class Connection:
    subprotocol = "eip.v1"

    def __init__(self):
        self.closed = asyncio.Event()

    async def close(self, code=1000, reason=""):
        self.closed.set()

    async def wait_closed(self):
        await self.closed.wait()


class Session:
    def __init__(self, connection):
        self.connection = connection
        self.descriptor = SimpleNamespace(available_methods=("file.read_text", *module.REQUIRED_METHODS))
        self.ready_calls = 0

    async def readiness(self):
        self.ready_calls += 1
        return SimpleNamespace(ready=True)

    async def close(self):
        await self.connection.close()

    async def abort(self):
        await self.connection.close()


@pytest.fixture
def initialized(monkeypatch):
    sessions = []

    async def initialize(transport, **kwargs):
        session = Session(transport._connection)
        sessions.append(session)
        return session

    monkeypatch.setattr(module.EIPSession, "initialize", initialize)
    return sessions


async def test_websocket_wait_attach_exclusive_close_and_reconnect(initialized):
    async with WebSocketEnvdConnections() as hub:
        context = hub.open_session(expected_environment_id="env-native", required_methods=frozenset(), timeout=1)
        acquire = asyncio.create_task(context.__aenter__())
        await asyncio.sleep(0)
        assert not acquire.done()
        connection = Connection()
        attachment = asyncio.create_task(hub.attach("env-native", connection))
        session = await acquire
        assert session is initialized[0]
        assert session.ready_calls == 1
        with pytest.raises(EnvironmentProviderError) as error:
            async with hub.open_session(expected_environment_id="env-native", required_methods=frozenset()):
                pytest.fail("cannot concurrently lease one daemon")
        assert error.value.code == "provider_connection_busy"
        duplicate = Connection()
        with pytest.raises(EnvironmentProviderError):
            await hub.attach("env-native", duplicate)
        assert duplicate.closed.is_set() and not connection.closed.is_set()
        await context.__aexit__(None, None, None)
        await attachment
        assert connection.closed.is_set()
        replacement = asyncio.create_task(hub.attach("env-native", Connection()))
        async with hub.open_session(
            expected_environment_id="env-native", required_methods=frozenset(), timeout=1
        ) as second:
            assert second is not session
        await replacement


async def test_websocket_shutdown_cancels_waiters_and_closes_idle_connections(initialized):
    hub = WebSocketEnvdConnections()
    attached = asyncio.create_task(hub.attach("env-native", Connection()))
    while not initialized:
        await asyncio.sleep(0)
    waiter = asyncio.create_task(
        hub.open_session(expected_environment_id="env-other", required_methods=frozenset()).__aenter__()
    )
    await asyncio.sleep(0)
    await hub.close()
    with pytest.raises(EnvironmentProviderError):
        await waiter
    await asyncio.gather(attached, return_exceptions=True)
    assert initialized[0].connection.closed.is_set()
    assert not hub._attachments


async def test_websocket_timeout_cancellation_and_capacity(initialized):
    async with WebSocketEnvdConnections(max_connections=1) as hub:
        with pytest.raises(EnvironmentProviderError) as error:
            async with hub.open_session(
                expected_environment_id="env-missing", required_methods=frozenset(), timeout=0.001
            ):
                pytest.fail("offline")
        assert error.value.code == "provider_connection_timeout"
        attach = asyncio.create_task(hub.attach("env-native", Connection()))
        while not initialized:
            await asyncio.sleep(0)
        excess = Connection()
        with pytest.raises(EnvironmentProviderError):
            await hub.attach("env-other", excess)
        assert excess.closed.is_set()
        with pytest.raises(EnvironmentProviderError):
            async with hub.open_session(
                expected_environment_id="env-native", required_methods=frozenset({"unsupported"})
            ):
                pytest.fail("unsupported")
        attach.cancel()
        await asyncio.gather(attach, return_exceptions=True)
        assert not hub._attachments


async def test_websocket_cancel_before_attachment_task_starts_releases_admission(initialized):
    hub = WebSocketEnvdConnections()
    connection = Connection()
    attachment = asyncio.create_task(hub.attach("env-native", connection))
    await asyncio.sleep(0)  # attach has scheduled _serve, but _serve has not run.
    attachment.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attachment
    assert connection.closed.is_set()
    assert not hub._attachments
    await hub.close()


async def test_websocket_failed_initialization_cleans_slot(monkeypatch):
    async def initialize(*args, **kwargs):
        raise RuntimeError("private endpoint or credential")

    monkeypatch.setattr(module.EIPSession, "initialize", initialize)
    async with WebSocketEnvdConnections() as hub:
        connection = Connection()
        with pytest.raises(EnvironmentProviderError) as error:
            await hub.attach("env-native", connection)
        assert "private" not in str(error.value)
        assert connection.closed.is_set() and not hub._attachments


async def test_websocket_cancelled_lease_cleans_only_its_connection(initialized):
    async with WebSocketEnvdConnections() as hub:
        first = Connection()
        second = Connection()
        attachments = [
            asyncio.create_task(hub.attach(identity, connection))
            for identity, connection in (("env-a", first), ("env-b", second))
        ]
        entered = asyncio.Event()

        async def use():
            async with hub.open_session(expected_environment_id="env-a", required_methods=frozenset()):
                entered.set()
                await asyncio.Event().wait()

        use_task = asyncio.create_task(use())
        await entered.wait()
        use_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await use_task
        assert first.closed.is_set() and not second.closed.is_set()
        async with hub.open_session(expected_environment_id="env-b", required_methods=frozenset()):
            pass
        await asyncio.gather(*attachments)


async def test_websocket_readiness_cancellation_stays_cancelled(monkeypatch):
    readiness_started = asyncio.Event()

    class TerminalReadinessSession(Session):
        terminal = False

        async def readiness(self):
            readiness_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                # The real EIPSession readiness path terminates on cancellation.
                self.terminal = True

        async def close(self):
            if self.terminal:
                raise RuntimeError("session is terminal")
            await super().close()

    async def initialize(transport, **kwargs):
        return TerminalReadinessSession(transport._connection)

    monkeypatch.setattr(module.EIPSession, "initialize", initialize)
    async with WebSocketEnvdConnections() as hub:
        connection = Connection()
        attached = asyncio.create_task(hub.attach("env-native", connection))
        acquire = asyncio.create_task(
            hub.open_session(expected_environment_id="env-native", required_methods=frozenset()).__aenter__()
        )
        await readiness_started.wait()
        acquire.cancel()
        with pytest.raises(asyncio.CancelledError):
            await acquire
        assert acquire.cancelled() and connection.closed.is_set()
        await attached


async def test_websocket_shutdown_aborts_a_pending_lease_close(monkeypatch):
    close_started = asyncio.Event()

    class PendingCloseSession(Session):
        async def close(self):
            close_started.set()
            await self.connection.closed.wait()

    async def initialize(transport, **kwargs):
        return PendingCloseSession(transport._connection)

    monkeypatch.setattr(module.EIPSession, "initialize", initialize)
    hub = WebSocketEnvdConnections()
    connection = Connection()
    attached = asyncio.create_task(hub.attach("env-native", connection))
    context = hub.open_session(expected_environment_id="env-native", required_methods=frozenset())
    await context.__aenter__()
    lease_close = asyncio.create_task(context.__aexit__(None, None, None))
    await close_started.wait()
    await hub.close()
    assert connection.closed.is_set()
    await asyncio.wait_for(lease_close, timeout=1)
    await asyncio.gather(attached, return_exceptions=True)
    assert not hub._attachments


async def test_http_failed_preparation_never_replays_connection_attempt(monkeypatch):
    from contextlib import asynccontextmanager

    from a13n_harness.providers.environment.remote_envd import http as http_module

    calls = []

    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        calls.append("connect")
        raise OSError("private endpoint")
        yield  # pragma: no cover

    monkeypatch.setattr(http_module.HttpEIPSessionSource, "open_session", unavailable)
    provider = HTTP_ENVD
    runtime = HttpEnvdProviderRuntime(
        HttpEnvdConnectionConfiguration(endpoint="https://envd.example"),
        HttpEnvdCredential(token=SecretStr("test-token")),
    )
    environment = provider.construct(
        configuration=RemoteEnvdEnvironmentConfiguration(), environment_id="env-logical", state=state(), runtime=runtime
    )
    for _ in range(2):
        with pytest.raises(EnvironmentProviderError):
            await environment.prepare()
    await environment.close()
    assert calls == ["connect"]
    assert not environment.recover_on_unavailable


async def test_websocket_concurrent_shutdown_does_not_recancel_cleanup(monkeypatch):
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()

    class SlowAbortSession(Session):
        async def abort(self):
            cleanup_started.set()
            await finish_cleanup.wait()
            await super().abort()

    async def initialize(transport, **kwargs):
        return SlowAbortSession(transport._connection)

    monkeypatch.setattr(module.EIPSession, "initialize", initialize)
    hub = WebSocketEnvdConnections()
    connection = Connection()
    attached = asyncio.create_task(hub.attach("env-native", connection))
    while not hub._attachments or hub._attachments["env-native"].session is None:
        await asyncio.sleep(0)
    first = asyncio.create_task(hub.close())
    await cleanup_started.wait()
    second = asyncio.create_task(hub.close())
    await asyncio.sleep(0)
    finish_cleanup.set()
    await asyncio.gather(first, second)
    await asyncio.gather(attached, return_exceptions=True)
    assert connection.closed.is_set() and not hub._attachments

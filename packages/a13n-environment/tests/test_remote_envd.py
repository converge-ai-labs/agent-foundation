"""Remote Provider codecs and Host-owned WebSocket lifecycle, without network I/O."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_envd_client import EIPSessionStateError
from a13n_environment import (
    EnvironmentProviderError,
    EnvironmentState,
    HttpEnvdBackendConfiguration,
    HttpEnvdCredential,
    HttpEnvdEnvironmentProvider,
    HttpEnvdProviderRuntime,
    RemoteEnvdProviderConfiguration,
    WebSocketEnvdBackendConfiguration,
    WebSocketEnvdConnections,
    WebSocketEnvdEnvironmentProvider,
    WebSocketEnvdProviderRuntime,
    build_environment_provider_catalog,
)
from a13n_environment.management import ProviderRuntimeContext
from a13n_environment.remote_envd import connections as module
from pydantic import SecretStr, ValidationError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def state(key="a13n.http-envd", native="env-native"):
    return EnvironmentState(provider_key=key, state_version="1", state={"device_id": native})


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
        HttpEnvdBackendConfiguration(endpoint=endpoint)


async def test_remote_catalog_codecs_are_inert_and_external_only(tmp_path):
    catalog = build_environment_provider_catalog(builtin_keys=("a13n.http-envd", "a13n.websocket-envd"))
    hub = WebSocketEnvdConnections()
    runtimes = (
        HttpEnvdProviderRuntime(
            HttpEnvdBackendConfiguration(endpoint="https://example.com/"), HttpEnvdCredential(token=SecretStr("secret"))
        ),
        WebSocketEnvdProviderRuntime(hub),
    )
    for provider, runtime in zip(catalog.values(), runtimes, strict=True):
        recipe = provider.validate_configuration(schema_version="1", value={})
        assert not provider.supports_managed
        assert not provider.supports_stop and not provider.supports_destroy and not provider.requires_keepalive
        assert provider.target_identity(configuration=recipe, state=state(provider.key)) == "env-native"
        environment = provider.create_environment(
            configuration=recipe, environment_id="env-logical", state=state(provider.key), runtime=runtime
        )
        await environment.enter(thread_id="t", run_id="r", agent_instance_id="a", mount_id="m")
        assert environment.environment_id == "env-logical"
        assert environment.descriptor.generation == "unprepared"
        assert environment.operations.files is None
        assert environment.dump_state() == state(provider.key)
        await environment.close()
        assert environment.dump_state() == state(provider.key)
        with pytest.raises(EnvironmentProviderError):
            provider.create_environment(configuration=recipe, environment_id="env-logical", state=None, runtime=runtime)
        for invalid in (
            state("wrong.provider"),
            EnvironmentState(provider_key=provider.key, state_version="2", state={}),
            EnvironmentState(
                provider_key=provider.key,
                state_version="1",
                state={"device_id": "env-native", "credential": "secret"},
            ),
        ):
            with pytest.raises(EnvironmentProviderError):
                provider.target_identity(configuration=recipe, state=invalid)
        with pytest.raises(EnvironmentProviderError):
            provider.validate_configuration(schema_version="2", value={})
        with pytest.raises(EnvironmentProviderError):
            provider.validate_configuration(schema_version="1", value={"endpoint": "not-a-recipe"})
    await hub.close()


async def test_lifecycle_never_prepares_or_destroys_external_daemon():
    provider = HttpEnvdEnvironmentProvider()
    runtime = HttpEnvdProviderRuntime(
        HttpEnvdBackendConfiguration(endpoint="https://example.com"), HttpEnvdCredential(token=SecretStr("secret"))
    )
    for action in ("stop", "destroy", "reconcile"):
        env = provider.create_environment(
            configuration=RemoteEnvdProviderConfiguration(),
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
    context = ProviderRuntimeContext(
        environment_id="env-logical", operation_id="op", storage_root=Path("/unused"), managed=False
    )
    provider = WebSocketEnvdEnvironmentProvider()
    with pytest.raises(EnvironmentProviderError, match="Envd provider") as error:
        await provider.create_runtime(
            configuration=WebSocketEnvdBackendConfiguration(), credential=None, context=context
        )
    assert error.value.code == "provider_runtime_required"
    async with WebSocketEnvdConnections() as hub:
        provider = WebSocketEnvdEnvironmentProvider(connections=hub)
        runtime = await provider.create_runtime(
            configuration=WebSocketEnvdBackendConfiguration(), credential=None, context=context
        )
        assert runtime.connections is hub
        with pytest.raises(EnvironmentProviderError) as error:
            await provider.create_runtime(
                configuration=WebSocketEnvdBackendConfiguration(),
                credential=None,
                context=ProviderRuntimeContext(environment_id="env", operation_id="op", storage_root=Path("/unused")),
            )
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
    def __init__(self, device):
        self.device = device
        self.closed = False
        self.ready_calls = 0

    async def readiness(self):
        self.ready_calls += 1
        return SimpleNamespace(ready=True)

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


class Device:
    session_class = Session

    def __init__(self, connection, identity):
        self.connection = connection
        self.descriptor = SimpleNamespace(device_id=identity, available_methods=("file.read_text",))
        self.sessions = []

    async def open_session(self, *, required_methods, working_directory=None):
        if set(required_methods) - set(self.descriptor.available_methods):
            raise EIPSessionStateError("unsupported")
        session = self.session_class(self)
        self.sessions.append(session)
        try:
            await session.readiness()
            return session
        except BaseException:
            await session.close()
            raise

    async def describe(self):
        return self.descriptor

    async def close(self):
        for session in self.sessions:
            await session.close()
        await self.connection.close()


@pytest.fixture
def initialized(monkeypatch):
    sessions = []

    async def initialize(transport, **kwargs):
        session = Device(transport._connection, kwargs["expected_device_id"])
        sessions.append(session)
        return session

    monkeypatch.setattr(module.EIPDeviceConnection, "initialize", initialize)
    return sessions


async def test_websocket_shared_scopes_leave_connection_alive_until_disconnect(initialized):
    async with WebSocketEnvdConnections() as hub:
        context = hub.open_session(expected_device_id="env-native", required_methods=frozenset(), timeout=1)
        acquire = asyncio.create_task(context.__aenter__())
        await asyncio.sleep(0)
        assert not acquire.done()
        connection = Connection()
        attachment = asyncio.create_task(hub.attach("env-native", connection))
        session = await acquire
        assert session is initialized[0].sessions[0]
        assert session.ready_calls == 1
        async with hub.open_session(expected_device_id="env-native", required_methods=frozenset()) as shared:
            assert shared is not session
            assert shared.device is session.device
        assert shared.closed and not session.closed
        assert not connection.closed.is_set()
        duplicate = Connection()
        with pytest.raises(EnvironmentProviderError):
            await hub.attach("env-native", duplicate)
        assert duplicate.closed.is_set() and not connection.closed.is_set()
        await context.__aexit__(None, None, None)
        assert not connection.closed.is_set()
        assert not attachment.done()
        await connection.close()
        await attachment
        replacement_connection = Connection()
        replacement = asyncio.create_task(hub.attach("env-native", replacement_connection))
        async with hub.open_session(expected_device_id="env-native", required_methods=frozenset(), timeout=1) as second:
            assert second is not session
        await replacement_connection.close()
        await replacement


async def test_websocket_shutdown_cancels_waiters_and_closes_idle_connections(initialized):
    hub = WebSocketEnvdConnections()
    attached = asyncio.create_task(hub.attach("env-native", Connection()))
    while not initialized:
        await asyncio.sleep(0)
    waiter = asyncio.create_task(
        hub.open_session(expected_device_id="env-other", required_methods=frozenset()).__aenter__()
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
            async with hub.open_session(expected_device_id="env-missing", required_methods=frozenset(), timeout=0.001):
                pytest.fail("offline")
        assert error.value.code == "provider_connection_timeout"
        attach = asyncio.create_task(hub.attach("env-native", Connection()))
        while not initialized:
            await asyncio.sleep(0)
        excess = Connection()
        with pytest.raises(EnvironmentProviderError):
            await hub.attach("env-other", excess)
        assert excess.closed.is_set()
        with pytest.raises(EIPSessionStateError):
            async with hub.open_session(expected_device_id="env-native", required_methods=frozenset({"unsupported"})):
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

    monkeypatch.setattr(module.EIPDeviceConnection, "initialize", initialize)
    async with WebSocketEnvdConnections() as hub:
        connection = Connection()
        with pytest.raises(EnvironmentProviderError) as error:
            await hub.attach("env-native", connection)
        assert "private" not in str(error.value)
        assert connection.closed.is_set() and not hub._attachments


async def test_websocket_cancelled_scope_keeps_shared_and_other_connections(initialized):
    async with WebSocketEnvdConnections() as hub:
        first = Connection()
        second = Connection()
        attachments = [
            asyncio.create_task(hub.attach(identity, connection))
            for identity, connection in (("env-a", first), ("env-b", second))
        ]
        entered = asyncio.Event()

        async def use():
            async with hub.open_session(expected_device_id="env-a", required_methods=frozenset()):
                entered.set()
                await asyncio.Event().wait()

        use_task = asyncio.create_task(use())
        await entered.wait()
        use_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await use_task
        assert not first.closed.is_set() and not second.closed.is_set()
        async with hub.open_session(expected_device_id="env-a", required_methods=frozenset()):
            pass
        async with hub.open_session(expected_device_id="env-b", required_methods=frozenset()):
            pass
        await hub.close()
        await asyncio.gather(*attachments, return_exceptions=True)


async def test_websocket_readiness_cancellation_stays_cancelled(monkeypatch):
    readiness_started = asyncio.Event()

    class PendingReadinessSession(Session):
        async def readiness(self):
            readiness_started.set()
            await asyncio.Event().wait()

    async def initialize(transport, **kwargs):
        device = Device(transport._connection, kwargs["expected_device_id"])
        device.session_class = PendingReadinessSession
        return device

    monkeypatch.setattr(module.EIPDeviceConnection, "initialize", initialize)
    async with WebSocketEnvdConnections() as hub:
        connection = Connection()
        attached = asyncio.create_task(hub.attach("env-native", connection))
        acquire = asyncio.create_task(
            hub.open_session(expected_device_id="env-native", required_methods=frozenset()).__aenter__()
        )
        await readiness_started.wait()
        acquire.cancel()
        with pytest.raises(asyncio.CancelledError):
            await acquire
        assert acquire.cancelled() and not connection.closed.is_set()
        await connection.close()
        await attached


async def test_websocket_scope_exit_closes_only_its_session(initialized):
    hub = WebSocketEnvdConnections()
    connection = Connection()
    attached = asyncio.create_task(hub.attach("env-native", connection))
    context = hub.open_session(expected_device_id="env-native", required_methods=frozenset())
    session = await context.__aenter__()
    await asyncio.wait_for(context.__aexit__(None, None, None), timeout=1)
    assert session.closed and not connection.closed.is_set()
    await hub.close()
    assert connection.closed.is_set()
    await asyncio.gather(attached, return_exceptions=True)
    assert not hub._attachments


async def test_http_failed_preparation_never_replays_connection_attempt(monkeypatch):
    from a13n_environment.remote_envd import http as http_module

    calls = []

    async def unavailable(*args, **kwargs):
        calls.append("connect")
        raise OSError("private endpoint")

    monkeypatch.setattr(http_module.EIPDeviceConnection, "initialize", unavailable)
    provider = HttpEnvdEnvironmentProvider()
    runtime = HttpEnvdProviderRuntime(
        HttpEnvdBackendConfiguration(endpoint="https://envd.example"), HttpEnvdCredential(token=SecretStr("test-token"))
    )
    environment = provider.create_environment(
        configuration=RemoteEnvdProviderConfiguration(), environment_id="env-logical", state=state(), runtime=runtime
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

    class SlowCloseDevice(Device):
        async def close(self):
            cleanup_started.set()
            await finish_cleanup.wait()
            await super().close()

    async def initialize(transport, **kwargs):
        return SlowCloseDevice(transport._connection, kwargs["expected_device_id"])

    monkeypatch.setattr(module.EIPDeviceConnection, "initialize", initialize)
    hub = WebSocketEnvdConnections()
    connection = Connection()
    attached = asyncio.create_task(hub.attach("env-native", connection))
    while not hub._attachments or hub._attachments["env-native"].device is None:
        await asyncio.sleep(0)
    first = asyncio.create_task(hub.close())
    await cleanup_started.wait()
    second = asyncio.create_task(hub.close())
    await asyncio.sleep(0)
    finish_cleanup.set()
    await asyncio.gather(first, second)
    await asyncio.gather(attached, return_exceptions=True)
    assert connection.closed.is_set() and not hub._attachments


async def test_websocket_device_describe_does_not_open_session(initialized):
    async with WebSocketEnvdConnections() as hub:
        connection = Connection()
        attached = asyncio.create_task(hub.attach("env-native", connection))
        assert (await hub.describe(expected_device_id="env-native")).device_id == "env-native"
        assert initialized[0].sessions == []
        await connection.close()
        await attached

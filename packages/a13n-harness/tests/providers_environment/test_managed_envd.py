"""Managed connection admission and lifetime are independent of vendor lifecycle."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.docker import http as docker_http
from a13n_harness.providers.environment.e2b import http as e2b_http
from a13n_harness.providers.environment.remote_envd import managed
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


@pytest.fixture
def peer(monkeypatch):
    events = []

    class Runtime:
        def __init__(self, connection, credential):
            events.append("connect")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            events.append("connection_closed")

        @asynccontextmanager
        async def open_session(self, **kwargs):
            events.append(kwargs)
            try:
                yield object()
            finally:
                events.append("session_closed")

    monkeypatch.setattr(managed, "HttpEnvdProviderRuntime", Runtime)
    return events


def connection(**kwargs):
    return managed.managed_http_runtime(
        provider_key="docker",
        target_id="container-test",
        connection=HttpEnvdConnectionConfiguration(endpoint="http://127.0.0.1:8888"),
        **kwargs,
    )


async def test_denied_endpoint_does_not_load_credentials_or_dial(peer):
    credentials = AsyncMock()
    with pytest.raises(EnvironmentProviderError, match="denies") as error:
        async with connection(endpoint_policy=EndpointPolicy(require_https=True), load_credential=credentials):
            pytest.fail("Denied endpoint")
    assert error.value.code == "envd_endpoint_denied"
    credentials.assert_not_awaited()
    assert peer == []


async def test_failed_credential_loading_does_not_dial_or_retry(peer):
    credentials = AsyncMock(side_effect=RuntimeError("credential unavailable"))
    with pytest.raises(RuntimeError, match="credential unavailable"):
        async with connection(endpoint_policy=EndpointPolicy(), load_credential=credentials):
            pytest.fail("Missing credential")
    credentials.assert_awaited_once()
    assert peer == []


@pytest.mark.parametrize("outcome", ["success", "unknown", "cancel"])
async def test_runtime_cleanup_without_session_or_replay(peer, outcome):
    credentials = AsyncMock(return_value=HttpEnvdCredential(token=SecretStr("test-token")))

    async def execute():
        async with connection(endpoint_policy=EndpointPolicy(), load_credential=credentials):
            if outcome == "unknown":
                raise TimeoutError("operation outcome unknown")
            if outcome == "cancel":
                raise asyncio.CancelledError()

    if outcome == "success":
        await execute()
    else:
        with pytest.raises(TimeoutError if outcome == "unknown" else asyncio.CancelledError):
            await execute()
    credentials.assert_awaited_once()
    assert peer == ["connect", "connection_closed"]


@pytest.fixture(params=[docker_http, e2b_http], ids=["docker", "e2b"])
def session_context(peer, monkeypatch, request):
    credentials = AsyncMock(return_value=HttpEnvdCredential(token=SecretStr("test-token")))
    monkeypatch.setattr(
        request.param,
        "http_runtime",
        lambda reference: connection(endpoint_policy=EndpointPolicy(), load_credential=credentials),
    )
    config = SimpleNamespace(desktop=True)
    reference = SimpleNamespace(allocation_id="env-fixed", config=config, _native_id="env-fixed", _configuration=config)
    return lambda **kwargs: request.param.http_session(reference, **kwargs)


@pytest.mark.parametrize("recover", [False, True])
async def test_provider_passes_recovery_to_session_not_device_runtime(peer, session_context, recover):
    recovery = EnvdSessionRecovery() if recover else None
    async with session_context(session_recovery=recovery):
        assert peer[1]["session_recovery"] is recovery
    assert peer[2:] == ["session_closed", "connection_closed"]


@pytest.mark.parametrize("outcome", ["success", "unknown", "cancel"])
async def test_provider_session_cleanup_without_replay(peer, session_context, outcome):
    async def execute():
        async with session_context():
            if outcome == "unknown":
                raise TimeoutError("operation outcome unknown")
            if outcome == "cancel":
                raise asyncio.CancelledError()

    if outcome == "success":
        await execute()
    else:
        with pytest.raises(TimeoutError if outcome == "unknown" else asyncio.CancelledError):
            await execute()
    assert peer[0] == "connect"
    assert peer[1]["expected_device_id"] == "env-fixed"
    assert peer[1]["working_directory"] == "/workspace"
    assert "computer.observe" in peer[1]["required_methods"]
    assert "environment.describe" in peer[1]["required_methods"]
    assert peer[2:] == ["session_closed", "connection_closed"]


async def test_failed_session_negotiation_closes_connection(peer, session_context, monkeypatch):
    @asynccontextmanager
    async def incompatible(self, **kwargs):
        raise RuntimeError("required capability unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(managed.HttpEnvdProviderRuntime, "open_session", incompatible)
    with pytest.raises(RuntimeError, match="required capability unavailable"):
        async with session_context():
            pytest.fail("Incompatible daemon")
    assert peer == ["connect", "connection_closed"]


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
async def test_monitoring_credential_cache_reuses_until_failed_observation(peer, outcome):
    credentials = AsyncMock(return_value=HttpEnvdCredential(token=SecretStr("private-token")))
    cache = managed.ManagedCredentialCache()
    kwargs = dict(endpoint_policy=EndpointPolicy(), load_credential=credentials, credential_cache=cache)
    for _ in range(3):
        async with connection(**kwargs):
            pass
    assert credentials.await_count == 1
    assert "private-token" not in repr(cache)
    error = TimeoutError if outcome == "failure" else asyncio.CancelledError
    with pytest.raises(error):
        async with connection(**kwargs):
            raise error()
    assert cache.credential is None
    assert credentials.await_count == 1  # No replay within a failed observation.
    async with connection(**kwargs):
        pass
    assert credentials.await_count == 2


@pytest.mark.parametrize("changed", ["provider", "target", "endpoint"])
async def test_monitoring_cache_requires_same_target_route_and_current_policy(peer, changed):
    credentials = AsyncMock(return_value=HttpEnvdCredential(token=SecretStr("private-token")))
    cache = managed.ManagedCredentialCache()
    kwargs = dict(
        provider_key="docker",
        target_id="one",
        endpoint_policy=EndpointPolicy(),
        connection=HttpEnvdConnectionConfiguration(endpoint="http://127.0.0.1:8888"),
        load_credential=credentials,
        credential_cache=cache,
    )
    async with managed.managed_http_runtime(**kwargs):
        pass
    with pytest.raises(EnvironmentProviderError):
        async with managed.managed_http_runtime(**{**kwargs, "endpoint_policy": EndpointPolicy(require_https=True)}):
            pytest.fail("A cached credential cannot bypass current endpoint policy")
    assert credentials.await_count == 1
    if changed == "provider":
        kwargs["provider_key"] = "e2b"
    elif changed == "target":
        kwargs["target_id"] = "two"
    else:
        kwargs["connection"] = HttpEnvdConnectionConfiguration(endpoint="http://127.0.0.1:8889")
    async with managed.managed_http_runtime(**kwargs):
        pass
    assert credentials.await_count == 2


async def test_e2b_monitoring_cache_keeps_vendor_discovery_fresh(peer, monkeypatch):
    sandbox = SimpleNamespace(
        get_host=lambda port: "sandbox.example.com",
        commands=SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(stdout="private-token"))),
    )
    opened = AsyncMock(return_value=sandbox)
    closed = AsyncMock()
    monkeypatch.setattr(e2b_http, "open_sandbox", opened)
    monkeypatch.setattr(e2b_http, "close_sandbox", closed)
    reference = SimpleNamespace(
        _state=SimpleNamespace(sandbox_id="sandbox-one"),
        _options=lambda: {},
        _validate_target=lambda info: None,
        _configuration=SimpleNamespace(request_timeout_seconds=5),
        _runtime=SimpleNamespace(endpoint_policy=EndpointPolicy()),
    )
    cache = managed.ManagedCredentialCache()
    for _ in range(3):
        async with e2b_http.http_runtime(reference, credential_cache=cache):
            pass
    assert opened.await_count == closed.await_count == 3
    opened.assert_awaited_with("sandbox-one", {}, reference._validate_target)
    sandbox.commands.run.assert_awaited_once()
    sandbox.get_host = lambda port: "new-route.example.com"
    async with e2b_http.http_runtime(reference, credential_cache=cache):
        pass
    assert sandbox.commands.run.await_count == 2
    opened.side_effect = RuntimeError("ownership changed")
    with pytest.raises(RuntimeError, match="ownership changed"):
        async with e2b_http.http_runtime(reference, credential_cache=cache):
            pytest.fail("Cached credential cannot bypass vendor validation")
    assert sandbox.commands.run.await_count == 2

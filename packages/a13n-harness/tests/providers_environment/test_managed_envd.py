"""Managed connection admission and lifetime are independent of vendor lifecycle."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.remote_envd import managed
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    RemoteEnvdEnvironmentConfiguration,
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
    return managed.managed_http_session(
        provider_key="docker",
        connection=HttpEnvdConnectionConfiguration(endpoint="http://127.0.0.1:8888"),
        expected_device_id="env-fixed",
        configuration=RemoteEnvdEnvironmentConfiguration(
            working_directory="/workspace", required_methods=("computer.observe",)
        ),
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
async def test_fixed_target_session_cleanup_without_replay(peer, outcome):
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
    assert peer[0] == "connect"
    assert peer[1]["expected_device_id"] == "env-fixed"
    assert peer[1]["working_directory"] == "/workspace"
    assert "computer.observe" in peer[1]["required_methods"]
    assert "environment.describe" in peer[1]["required_methods"]
    assert peer[2:] == ["session_closed", "connection_closed"]


async def test_failed_session_negotiation_closes_connection(peer, monkeypatch):
    @asynccontextmanager
    async def incompatible(self, **kwargs):
        raise RuntimeError("required capability unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(managed.HttpEnvdProviderRuntime, "open_session", incompatible)
    credentials = AsyncMock(return_value=HttpEnvdCredential(token=SecretStr("test-token")))
    with pytest.raises(RuntimeError, match="required capability unavailable"):
        async with connection(endpoint_policy=EndpointPolicy(), load_credential=credentials):
            pytest.fail("Incompatible daemon")
    assert peer == ["connect", "connection_closed"]

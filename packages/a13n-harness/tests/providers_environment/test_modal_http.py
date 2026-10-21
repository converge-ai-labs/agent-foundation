"""Modal execution uses EIP and never allocates or repairs its target."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.configuration import RunConfiguration
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.modal import http
from a13n_harness.providers.environment.modal.provider import MODAL, construct
from a13n_harness.providers.environment.modal.shared import (
    ModalConnectionConfiguration,
    ModalCredential,
    ModalEnvironmentConfiguration,
)
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.remote_envd import managed
from a13n_harness.providers.environment.remote_envd.environment import RemoteEnvdExecution


def reference(policy=None):
    config = ModalEnvironmentConfiguration(image="im-envd", desktop=True)
    connection = ModalConnectionConfiguration(workspace="test", app_name="test")
    connection._endpoint_policy = policy or EndpointPolicy()
    state = EnvironmentState(
        provider_key="modal",
        state_version="1",
        state={
            "target_id": "sb-fixed",
            "environment_id": "env-fixed",
            "configuration_fingerprint": config.fingerprint,
        },
    )
    return http.HttpReference(
        config, "env-fixed", state, NativeRuntime(connection, ModalCredential(token_id="id", token_secret="secret"))
    )


@pytest.mark.parametrize("value", [{}, {"image": "im-envd", "root": "/tmp"}, {"image": "im-envd", "python": "python3"}])
def test_requires_explicit_envd_image(value):
    with pytest.raises(EnvironmentProviderError):
        MODAL.validate_environment(value)


def test_execution_uses_shared_eip_without_io():
    ref = reference()
    assert isinstance(
        construct(
            configuration=ref.config,
            environment_id=ref.environment_id,
            state=ref.state,
            runtime=NativeRuntime(ref.backend, ref.credential),
        ),
        RemoteEnvdExecution,
    )


@pytest.fixture
def peer(monkeypatch):
    tunnels = AsyncMock(return_value={8888: SimpleNamespace(url="https://8888-sandbox.example")})
    lookup = AsyncMock(return_value=SimpleNamespace(tunnels=SimpleNamespace(aio=tunnels)))
    closed = AsyncMock()
    command = AsyncMock(return_value="envd-secret")
    monkeypatch.setattr(http.HttpReference, "lookup", lookup)
    monkeypatch.setattr(http.HttpReference, "close_transport", closed)
    monkeypatch.setattr(http, "execute_command", command)
    events = []

    class Runtime:
        def __init__(self, connection, credential):
            assert connection.endpoint == "https://8888-sandbox.example"
            assert credential.token.get_secret_value() == "envd-secret"
            assert "envd-secret" not in repr(credential)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            events.append("closed")

        @asynccontextmanager
        async def open_session(self, **kwargs):
            events.append(kwargs)
            yield object()

    monkeypatch.setattr(managed, "HttpEnvdProviderRuntime", Runtime)
    return tunnels, lookup, closed, command, events


@pytest.mark.anyio
async def test_fixed_identity_recovery_and_cleanup_without_replay(peer):
    tunnels, lookup, closed, command, events = peer
    recovery = EnvdSessionRecovery()
    with pytest.raises(TimeoutError):
        async with http.http_session(reference(), session_recovery=recovery):
            raise TimeoutError("uncertain input")
    lookup.assert_awaited_once()
    tunnels.assert_awaited_once()
    command.assert_awaited_once()
    assert "http-token" in command.call_args.args[1][-1]
    assert events[0]["expected_device_id"] == "env-fixed"
    assert events[0]["session_recovery"] is recovery
    assert events[0]["working_directory"] == "/workspace"
    assert "computer.observe" in events[0]["required_methods"]
    assert events[1:] == ["closed"]
    closed.assert_awaited_once()


@pytest.mark.anyio
async def test_denied_route_never_reads_guest_token(peer):
    _, _, closed, command, events = peer
    policy = EndpointPolicy(configuration=RunConfiguration(allowed_hosts=frozenset({"other.example"})))
    with pytest.raises(EnvironmentProviderError, match="denies"):
        async with http.http_session(reference(policy)):
            pytest.fail("Denied route")
    command.assert_not_awaited()
    closed.assert_awaited_once()
    assert not events


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["missing", "tunnel", "http", "path", "userinfo", "token"])
async def test_failed_attach_never_bootstraps(peer, fault):
    tunnels, lookup, closed, command, events = peer
    if fault == "missing":
        lookup.return_value = None
    elif fault == "tunnel":
        tunnels.return_value = {}
    elif fault == "token":
        command.return_value = "invalid\n"
    else:
        tunnels.return_value[8888].url = {
            "http": "http://127.0.0.1",
            "path": "https://example.com/path",
            "userinfo": "https://secret@example.com",
        }[fault]
    with pytest.raises(EnvironmentProviderError):
        async with http.http_session(reference()):
            pytest.fail("Invalid target")
    if fault != "token":
        command.assert_not_awaited()
    closed.assert_awaited_once()
    assert not events

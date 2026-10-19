"""Daytona fixed-target Envd attachment never performs native Agent operations."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.configuration import RunConfiguration
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.daytona import http
from a13n_harness.providers.environment.daytona.provider import DAYTONA, construct
from a13n_harness.providers.environment.daytona.shared import (
    DaytonaConnectionConfiguration,
    DaytonaEnvironmentConfiguration,
    Sandbox,
)
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.remote_envd import managed
from a13n_harness.providers.environment.remote_envd.environment import RemoteEnvdExecution


def reference(policy=None):
    config = DaytonaEnvironmentConfiguration(snapshot="envd", desktop=True)
    connection = DaytonaConnectionConfiguration(organization_id="org")
    connection._endpoint_policy = policy or EndpointPolicy()
    state = EnvironmentState(
        provider_key="daytona",
        state_version="1",
        state={
            "target_id": "sb-fixed",
            "environment_id": "env-fixed",
            "configuration_fingerprint": config.fingerprint,
        },
    )
    return http.HttpReference(
        config, "env-fixed", state, NativeRuntime(connection, TokenCredential(api_key="control-secret"))
    )


@pytest.mark.parametrize(
    "value", [{}, {"snapshot": "envd", "root": "/home/daytona"}, {"snapshot": "envd", "execution": "native"}]
)
def test_only_envd_snapshots_are_supported(value):
    with pytest.raises(EnvironmentProviderError):
        DAYTONA.validate_environment(value)


def test_execution_is_shared_eip_without_io():
    ref = reference()
    assert isinstance(
        construct(
            configuration=ref.config,
            environment_id=ref.environment_id,
            state=ref.state,
            runtime=NativeRuntime(ref.backend, TokenCredential(api_key=ref.token)),
        ),
        RemoteEnvdExecution,
    )


@pytest.fixture
def peer(monkeypatch):
    cloud = SimpleNamespace(
        request=AsyncMock(return_value={"url": "https://8888-sandbox.example", "token": "proxy-secret"})
    )
    guest = SimpleNamespace(request=AsyncMock(return_value={"exitCode": 0, "result": "envd-secret"}))
    target = Sandbox(id="sb-fixed", name="fixed", organizationId="org", state="started", labels={})
    lookup = AsyncMock(return_value=target)
    closed = AsyncMock()
    monkeypatch.setattr(http.HttpReference, "lookup", lookup)
    monkeypatch.setattr(http.HttpReference, "transport", lambda self: cloud)
    monkeypatch.setattr(http.HttpReference, "open_toolbox", AsyncMock(return_value=guest))
    monkeypatch.setattr(http.HttpReference, "close_transport", closed)
    events = []

    class Runtime:
        def __init__(self, connection, credential):
            assert connection.endpoint == "https://8888-sandbox.example"
            assert credential.token.get_secret_value() == "envd-secret"
            assert credential.headers["X-Daytona-Preview-Token"].get_secret_value() == "proxy-secret"
            assert "secret" not in repr(credential)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            events.append("closed")

        @asynccontextmanager
        async def open_session(self, **kwargs):
            events.append(kwargs)
            yield object()

    monkeypatch.setattr(managed, "HttpEnvdProviderRuntime", Runtime)
    return cloud, guest, lookup, closed, events


@pytest.mark.anyio
async def test_attach_authenticates_fixed_identity_and_does_not_replay(peer):
    cloud, guest, lookup, closed, events = peer
    for _ in range(2):
        async with http.http_session(reference()):
            pass
    assert lookup.await_count == closed.await_count == 2
    assert guest.request.await_count == 2
    assert all(
        call.args[:2] == ("GET", "sandbox/sb-fixed/ports/8888/preview-url") for call in cloud.request.call_args_list
    )
    assert all("http-token" in call.kwargs["body"]["command"] for call in guest.request.call_args_list)
    assert events[0]["expected_device_id"] == "env-fixed"
    assert events[0]["working_directory"] == "/workspace"
    assert "computer.observe" in events[0]["required_methods"]
    with pytest.raises(TimeoutError):
        async with http.http_session(reference()):
            raise TimeoutError("uncertain input")
    assert lookup.await_count == closed.await_count == 3


@pytest.mark.anyio
async def test_denied_route_does_not_read_credential(peer):
    _, guest, _, closed, events = peer
    policy = EndpointPolicy(configuration=RunConfiguration(allowed_hosts=frozenset({"other.example"})))
    with pytest.raises(EnvironmentProviderError, match="denies"):
        async with http.http_session(reference(policy)):
            pytest.fail("Denied route")
    guest.request.assert_not_awaited()
    closed.assert_awaited_once()
    assert not events


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["missing", "stopped", "expiring", "credential", "token"])
async def test_attach_fails_without_bootstrap_or_fallback(peer, fault):
    cloud, guest, lookup, closed, events = peer
    if fault == "missing":
        lookup.return_value = None
    elif fault == "stopped":
        lookup.return_value.state = "stopped"
    elif fault == "expiring":
        lookup.return_value.autoDestroyAt = "soon"
    elif fault == "credential":
        guest.request.return_value = {"exitCode": 1, "result": "no credential"}
    else:
        guest.request.return_value = {"exitCode": 0, "result": "invalid token\n"}
    with pytest.raises(EnvironmentProviderError):
        async with http.http_session(reference()):
            pytest.fail("Invalid target")
    closed.assert_awaited_once()
    assert not events
    if fault in {"missing", "stopped", "expiring"}:
        cloud.request.assert_not_awaited()

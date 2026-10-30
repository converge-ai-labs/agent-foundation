"""E2B Native boundaries and fixed-target Envd attachment, without cloud resources."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.configuration import RunConfiguration
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.e2b import http
from a13n_harness.providers.environment.e2b.configuration import E2BEnvironmentConfiguration
from a13n_harness.providers.environment.e2b.provider import E2B
from a13n_harness.providers.environment.e2b.shared import E2BProviderRuntime
from a13n_harness.providers.environment.remote_envd import managed
from a13n_harness.providers.environment.remote_envd.environment import RemoteEnvdExecution
from pydantic import SecretStr, ValidationError


def reference(policy=None):
    config = E2BEnvironmentConfiguration(execution="envd_http", desktop=True, template="team-envd")
    state = EnvironmentState(
        provider_key="e2b",
        state_version="1",
        state={
            "sandbox_id": "sb-fixed",
            "environment_id": "env-fixed",
            "configuration_fingerprint": config.fingerprint,
        },
    )
    return http.HttpReference(
        config,
        environment_id="env-fixed",
        state=state,
        runtime=E2BProviderRuntime(SecretStr("account-secret"), endpoint_policy=policy or EndpointPolicy()),
    )


@pytest.mark.parametrize(
    "configuration", [{"desktop": True}, {"execution": "envd_http"}, {"execution": "envd_http", "template": "desktop"}]
)
def test_invalid_execution_prerequisites(configuration):
    with pytest.raises(ValidationError):
        E2BEnvironmentConfiguration(**configuration)


def test_native_and_envd_are_distinct_execution_paths():
    ref = reference()
    from a13n_harness.providers.environment.e2b.provider import _construct_execution, _describe

    assert isinstance(
        _construct_execution(
            configuration=ref._configuration, environment_id="env-fixed", state=ref.state, runtime=ref._runtime
        ),
        RemoteEnvdExecution,
    )
    config = E2BEnvironmentConfiguration()
    native = _describe(config)
    assert native.operation_families == frozenset({"files", "shell", "processes"})
    assert not any(
        a.value.startswith(("environment.port.", "environment.computer.")) for a in native.permissions.operations
    )
    # No provider lookup is performed while constructing a native connector.
    assert E2B.environment_model().execution == "native"


@pytest.fixture
def peer(monkeypatch):
    sandbox = SimpleNamespace(
        get_host=lambda port: f"{port}-sb-fixed.e2b.app",
        commands=SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(stdout="instance-token"))),
    )
    opened, closed = AsyncMock(return_value=sandbox), AsyncMock()
    monkeypatch.setattr(http, "open_sandbox", opened)
    monkeypatch.setattr(http, "close_sandbox", closed)
    events = []

    class Runtime:
        def __init__(self, connection, credential):
            assert credential.token.get_secret_value() == "instance-token"
            assert connection.endpoint == "https://8888-sb-fixed.e2b.app"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            events.append("closed")

        @asynccontextmanager
        async def open_session(self, **kwargs):
            events.append(kwargs)
            yield object()

    monkeypatch.setattr(managed, "HttpEnvdProviderRuntime", Runtime)
    return sandbox, opened, closed, events


@pytest.mark.anyio
async def test_attach_revalidates_target_and_cleans_up_without_lifecycle(peer):
    sandbox, opened, closed, events = peer
    ref = reference()
    for _ in range(2):
        with pytest.raises(TimeoutError):
            async with http.http_session(ref):
                raise TimeoutError("uncertain input")
    assert opened.await_count == closed.await_count == 2
    assert sandbox.commands.run.await_count == 2
    assert events[0]["expected_device_id"] == "env-fixed"
    assert "computer.observe" in events[0]["required_methods"]
    assert events[0]["working_directory"] == "/workspace"
    assert events[1] == events[3] == "closed"


@pytest.mark.anyio
async def test_endpoint_denial_does_not_read_guest_credential(peer):
    sandbox, _, closed, events = peer
    with pytest.raises(EnvironmentProviderError, match="denies"):
        async with http.http_session(
            reference(EndpointPolicy(configuration=RunConfiguration(allowed_hosts=frozenset({"other.example"}))))
        ):
            pytest.fail("Denied endpoint")
    sandbox.commands.run.assert_not_awaited()
    closed.assert_awaited_once()
    assert not events

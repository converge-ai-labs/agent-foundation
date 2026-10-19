"""Direct EIP attachment: routing, identity, credentials and policy remain separate."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.docker import http
from a13n_harness.providers.environment.docker.configuration import (
    DockerEnvironmentConfiguration,
    DockerProviderStateData,
)
from a13n_harness.providers.environment.docker.connection import DockerHttpConfiguration
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine
from a13n_harness.providers.environment.docker.shared import _fingerprint
from a13n_harness.providers.environment.remote_envd import managed

pytestmark = pytest.mark.anyio


def reference(identity="env_one", port=32001, *, desktop=True, policy=None):
    config = DockerEnvironmentConfiguration(execution="envd_http", desktop=desktop)
    state = EnvironmentState(
        provider_key="docker",
        state_version="1",
        state=DockerProviderStateData(
            environment_id=identity,
            container_id=f"container-{identity}",
            configuration_fingerprint=_fingerprint(config),
        ).model_dump(mode="json"),
    )
    container = Mock(
        status="running",
        attrs={"NetworkSettings": {"Ports": {"8888/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]}}},
    )
    engine = DockerSDKEngine(Mock())
    engine.client.containers.get.return_value = container
    ref = http.HttpReference(
        config,
        identity,
        state,
        DockerProviderRuntime(engine, DockerHttpConfiguration(host="127.0.0.1"), policy or EndpointPolicy()),
    )
    container.labels = ref.labels
    return ref, container


@pytest.fixture
def peer(monkeypatch):
    calls = []
    closed = []

    class Runtime:
        def __init__(self, configuration, credential):
            self.configuration, self.credential = configuration, credential

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(self.configuration.endpoint)

        @asynccontextmanager
        async def open_session(self, **kwargs):
            calls.append((self.configuration.endpoint, self.credential, kwargs))
            yield object()

    monkeypatch.setattr(managed, "HttpEnvdProviderRuntime", Runtime)
    monkeypatch.setattr(
        http.DockerCommands, "execute", AsyncMock(side_effect=[b"", b"instance-one", b"", b"instance-two"])
    )
    return calls, closed


async def test_instances_use_their_own_published_endpoint_credential_and_device(monkeypatch, peer):
    calls, closed = peer
    for identity, port in [("env_one", 32001), ("env_two", 32002)]:
        ref, _ = reference(identity, port)
        async with http.http_session(ref):
            pass
        ref.runtime.engine.client.containers.get.assert_called_once_with(f"container-{identity}")
        ref.runtime.engine.client.containers.create.assert_not_called()
        assert "instance-" not in str(ref.state)
    assert [call[0] for call in calls] == ["http://127.0.0.1:32001", "http://127.0.0.1:32002"]
    assert calls[0][1].token != calls[1][1].token
    assert [call[2]["expected_device_id"] for call in calls] == ["env_one", "env_two"]
    assert "computer.observe" in calls[0][2]["required_methods"]
    assert len(closed) == 2


async def test_headless_eip_does_not_require_computer_methods(peer):
    ref, _ = reference(desktop=False)
    async with http.http_session(ref):
        pass
    assert "computer.observe" not in peer[0][0][2]["required_methods"]


async def test_reconnect_rediscovers_port_on_same_container(peer):
    ref, container = reference()
    async with http.http_session(ref):
        pass
    container.attrs["NetworkSettings"]["Ports"]["8888/tcp"][0]["HostPort"] = "32100"
    async with http.http_session(ref):
        pass
    assert peer[0][1][0] == "http://127.0.0.1:32100"
    assert peer[0][0][2]["expected_device_id"] == peer[0][1][2]["expected_device_id"]


async def test_actual_endpoint_is_checked_before_reading_secret_or_dialing(peer):
    ref, _ = reference(policy=EndpointPolicy(require_https=True))
    with pytest.raises(EnvironmentProviderError) as raised:
        async with http.http_session(ref):
            pytest.fail("Must reject plaintext under HTTPS-only policy")
    assert raised.value.code == "envd_endpoint_denied"
    http.DockerCommands.execute.assert_not_awaited()
    assert peer[0] == []


@pytest.mark.parametrize(
    "bindings", [None, [], [{"HostIp": "0.0.0.0", "HostPort": "32001"}], [{"HostIp": "127.0.0.1", "HostPort": "70000"}]]
)
async def test_bad_or_unexpected_binding_never_dials(peer, bindings):
    ref, container = reference()
    container.attrs["NetworkSettings"]["Ports"]["8888/tcp"] = bindings
    with pytest.raises(EnvironmentProviderError) as raised:
        async with http.http_session(ref):
            pytest.fail("Must reject binding")
    assert raised.value.code == "envd_http_binding_invalid"
    assert peer[0] == []


async def test_stopped_container_is_not_started_by_attachment(peer):
    ref, container = reference()
    container.status = "exited"
    with pytest.raises(EnvironmentProviderError) as raised:
        async with http.http_session(ref):
            pytest.fail("Must reject stopped target")
    assert raised.value.code == "environment_stopped"
    container.start.assert_not_called()
    assert peer[0] == []


@pytest.mark.parametrize(
    "host", ["http://host", "host:9000", "user@host", "host/path", "host?x", "host#x", "host name"]
)
def test_http_host_is_not_a_url_or_docker_host(host):
    with pytest.raises(ValueError):
        DockerHttpConfiguration(host=host, allow_plaintext_private_link=True)


def test_remote_plaintext_requires_explicit_private_link_consent():
    with pytest.raises(ValueError):
        DockerHttpConfiguration(host="10.0.0.8")
    assert DockerHttpConfiguration(host="10.0.0.8", allow_plaintext_private_link=True).host == "10.0.0.8"


def test_network_disabled_and_native_desktop_are_rejected():
    with pytest.raises(ValueError, match="requires networking"):
        DockerEnvironmentConfiguration(execution="envd_http", disable_network=True)
    with pytest.raises(ValueError, match="requires envd_http"):
        DockerEnvironmentConfiguration(desktop=True)

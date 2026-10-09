"""Native Docker lifecycle contracts without an Envd dependency."""

import asyncio
import math
import threading
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_environment.docker.commands import DockerCommands
from a13n_environment.docker.configuration import (
    DockerEnvironmentConfiguration,
    DockerMountConfiguration,
)
from a13n_environment.docker.provider import DOCKER, DockerTarget
from a13n_environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine
from a13n_environment.errors import EnvironmentProviderError
from a13n_environment.models import EnvironmentError
from docker.errors import ImageNotFound, NotFound
from requests.exceptions import ConnectionError

pytestmark = pytest.mark.anyio


@pytest.fixture
def native(monkeypatch):
    engine = DockerSDKEngine(Mock())
    engine.client.containers.get.side_effect = NotFound("missing")
    container = Mock(id="a" * 64, status="created")
    container.start.side_effect = lambda: setattr(container, "status", "running")
    engine.client.containers.create.return_value = container
    engine.client.images.get.return_value.labels = {}
    engine.client.images.pull.return_value.labels = {}
    engine.client.images.get.return_value.id = "sha256:" + "f" * 64
    engine.client.images.pull.return_value.id = "sha256:" + "e" * 64
    monkeypatch.setattr(DockerCommands, "execute", AsyncMock(return_value=b""))
    config = DockerEnvironmentConfiguration(image="python:3.13-slim", memory_gb=0.25)
    env = DockerTarget(config, "env_test", None, DockerProviderRuntime(engine))
    container.labels = env.labels
    return env, engine, container


async def test_create_native_container_records_state_and_overrides_entrypoint(native):
    env, engine, container = native
    await env.create()
    try:
        options = engine.client.containers.create.call_args.kwargs
        assert options["init"] is True and options["working_dir"] == "/workspace"
        assert options["entrypoint"] == ["python3", "-I", "-c"]
        assert options["network_mode"] == "bridge"
        assert options["user"] is None
        assert options["mem_limit"] == 250_000_000
        engine.client.images.get.assert_called_once_with("python:3.13-slim")
        assert engine.client.containers.create.call_args.args[0] == "sha256:" + "f" * 64
        engine.client.images.pull.assert_not_called()
        assert "ports" not in options and "volumes" not in options
        assert env.state.state["container_id"] == container.id
        assert env.descriptor.backing_identity == container.id
    finally:
        await env.close()
    container.remove.assert_not_called()
    container.stop.assert_not_called()


async def test_connector_is_inert_and_opens_only_a_saved_running_target(native):
    env, engine, container = native
    await env.create()
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    engine.client.reset_mock()
    container.reset_mock()
    connector = DOCKER.execution_connector(
        env.config, environment_id=env.environment_id, state=env.state, runtime=env.runtime
    )
    assert engine.client.mock_calls == []
    async with await connector.open() as execution:
        assert execution.availability.status == "available"
        assert execution.operations.files is not None
        assert execution.state == env.state
        await execution.check_ready(frozenset({"files"}))
    engine.client.containers.create.assert_not_called()
    container.start.assert_not_called()
    container.stop.assert_not_called()
    container.remove.assert_not_called()
    engine.client.close.assert_not_called()


@pytest.mark.parametrize("status", ["exited", "missing"])
async def test_open_never_starts_or_replaces_a_target(native, status):
    env, engine, container = native
    await env.create()
    if status != "missing":
        engine.client.containers.get.side_effect = None
        engine.client.containers.get.return_value = container
        container.status = status
    engine.client.containers.create.reset_mock()
    container.reset_mock()
    connector = DOCKER.execution_connector(
        env.config, environment_id=env.environment_id, state=env.state, runtime=env.runtime
    )
    with pytest.raises((EnvironmentProviderError, EnvironmentError)):
        await connector.open()
    engine.client.containers.create.assert_not_called()
    container.start.assert_not_called()


async def test_execution_client_outlives_management_provider(native):
    env, engine, container = native
    provider = await DOCKER.open_provider(runtime=env.runtime)
    state = await provider.create(env.config, environment_id=env.environment_id, operation_id="op-create")
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    connector = provider.execution_connector(env.config, environment_id=env.environment_id, state=state)
    async with await connector.open() as execution:
        await provider.close()
        await execution.check_ready(frozenset({"files"}))
        engine.client.close.assert_not_called()


async def test_missing_image_pulls_once(native):
    env, engine, _ = native
    engine.client.images.get.side_effect = ImageNotFound("absent")
    await env.create()
    engine.client.images.pull.assert_called_once_with(env.config.image)
    assert engine.client.containers.create.call_args.args[0] == "sha256:" + "e" * 64
    await env.close()


def test_decimal_memory_preserves_docker_minimum_and_64_bit_limit():
    with pytest.raises(ValueError):
        DockerEnvironmentConfiguration(memory_gb=0.006291455)
    assert DockerEnvironmentConfiguration(memory_gb=0.006291456).memory_gb == 0.006291456
    maximum_gb = (2**63 - 1) / 1_000_000_000
    below_maximum = math.nextafter(maximum_gb, 0)
    assert int(DockerEnvironmentConfiguration(memory_gb=below_maximum).memory_gb * 1_000_000_000) <= 2**63 - 1
    for value in (maximum_gb, math.nextafter(maximum_gb, math.inf), 1e308):
        with pytest.raises(ValueError):
            DockerEnvironmentConfiguration(memory_gb=value)


async def test_decimal_memory_minimum_reaches_docker_as_bytes(native):
    env, engine, _ = native
    env = DockerTarget(
        DockerEnvironmentConfiguration(image=env.config.image, memory_gb=0.006291456),
        env.environment_id,
        None,
        env.runtime,
    )
    await env.create()
    assert engine.client.containers.create.call_args.kwargs["mem_limit"] == 6_291_456
    await env.close()


async def test_cancelled_creation_waits_for_inflight_docker_call(native, monkeypatch):
    env, engine, _ = native
    created = threading.Event()
    release = threading.Event()
    original = env._create

    def delayed_create():
        container = original()
        created.set()
        release.wait(5)
        return container

    monkeypatch.setattr(env, "_create", delayed_create)
    task = asyncio.create_task(env.create())
    assert await asyncio.to_thread(created.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    engine.client.containers.create.assert_called_once()


async def test_start_failure_preserves_allocated_identity(native):
    env, _, container = native
    container.start.side_effect = ConnectionError("lost response")
    with pytest.raises(EnvironmentProviderError) as error:
        await env.create()
    assert error.value.certainty == "unknown"
    assert env.state.state["container_id"] == container.id
    await env.close()


async def test_reentry_reuses_container_without_replaying_initialization(native):
    env, engine, container = native
    await env.create()
    await env.close()
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    fresh = DockerTarget(env.config, env.environment_id, env.state, env.runtime)
    DockerCommands.execute.reset_mock()
    await fresh.open(execution_id="exec-reentry")
    assert DockerCommands.execute.await_count == 1
    assert "Initialization incomplete" in DockerCommands.execute.call_args.args[0][-1]
    assert engine.client.containers.create.call_count == 1
    await fresh.close()


async def test_confirmed_absence_never_rebuilds_a_saved_target(native):
    env, engine, _container = native
    await env.create()
    fresh = DockerTarget(env.config, env.environment_id, env.state, env.runtime)
    with pytest.raises(EnvironmentProviderError) as missing:
        await fresh.create()
    assert missing.value.category == "missing"
    assert engine.client.containers.create.call_count == 1


async def test_connection_failure_never_creates_speculative_replacement(native):
    env, engine, _ = native
    engine.client.containers.get.side_effect = ConnectionError("unreachable")
    with pytest.raises(EnvironmentProviderError):
        await env.create()
    engine.client.containers.create.assert_not_called()


async def test_foreign_container_cannot_be_adopted_or_destroyed(native):
    env, engine, container = native
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    container.labels = {"a13n.environment": "env_other"}
    with pytest.raises(EnvironmentError, match="another configuration"):
        await env.create()
    with pytest.raises(EnvironmentError):
        await env.destroy()
    container.start.assert_not_called()
    container.remove.assert_not_called()


async def test_reconcile_recovers_lost_create_response_without_starting(native):
    env, engine, container = native
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    assert await env.inspect() == "stopped"
    assert env.state.state["container_id"] == container.id
    container.start.assert_not_called()
    engine.client.containers.create.assert_not_called()


async def test_external_registration_keeps_allocation_identity(native):
    env, engine, container = native
    await env.create()
    await env.close()
    container.status = "running"
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    external = DockerTarget(env.config, "env_registered", env.state, DockerProviderRuntime(engine))
    await external.open(execution_id="exec-external")
    assert external.environment_id == "env_registered"
    assert external.state.state["environment_id"] == env.environment_id
    await external.close()


async def test_unknown_remove_outcome_preserves_state(native):
    env, engine, container = native
    await env.create()
    await env.close()
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    container.remove.side_effect = ConnectionError("lost response")
    fresh = DockerTarget(env.config, env.environment_id, env.state, env.runtime)
    with pytest.raises(EnvironmentProviderError) as error:
        await fresh.destroy()
    assert error.value.certainty == "unknown"
    assert fresh.state == env.state


@pytest.mark.parametrize("target", ["/", "/workspace", "/tmp", "/tmp/a13n", "/tmp/a13n/nested"])
def test_mounts_cannot_replace_private_storage(target):
    with pytest.raises(ValueError):
        DockerMountConfiguration(source="/data", target=target)


def test_mount_beside_private_storage_is_accepted():
    # Only path components count: a shared string prefix is not the private directory.
    assert str(DockerMountConfiguration(source="/data", target="/tmp/a13n-cache").target) == "/tmp/a13n-cache"


def test_configuration_rejects_unknown_keys():
    # A misspelled limit must fail instead of being silently ignored.
    with pytest.raises(ValueError):
        DockerEnvironmentConfiguration.model_validate({"memory_mib": 256})


async def test_missing_external_target_never_adopts_same_name_replacement(native):
    env, engine, _ = native
    await env.create()
    await env.close()
    engine.client.containers.get.reset_mock()
    external = DockerTarget(env.config, "env_registered", env.state, DockerProviderRuntime(engine))
    assert await external.inspect() == "absent"
    engine.client.containers.get.assert_called_once_with(env.state.state["container_id"])
    with pytest.raises(EnvironmentProviderError) as error:
        await external.open(execution_id="exec-external")
    assert error.value.category == "missing"
    assert engine.client.containers.create.call_count == 1
    await external.close()


async def test_cancelled_engine_acquisition_closes_the_engine_it_owns(monkeypatch):
    """A cancelled caller must never leave an acquired Docker client open."""
    acquiring, release, closed = threading.Event(), threading.Event(), asyncio.Event()
    engine = DockerSDKEngine(Mock())

    def connect(docker_host, *, timeout_seconds=60):
        del docker_host, timeout_seconds
        acquiring.set()
        release.wait(5)
        return engine

    def close() -> None:
        closed.set()

    engine.client.close.side_effect = close
    monkeypatch.setattr(DockerSDKEngine, "connect", connect)
    acquisition = asyncio.create_task(
        DOCKER.open_provider(configuration={"docker_host": "unix:///var/run/docker.sock"})
    )
    await asyncio.to_thread(acquiring.wait, 5)
    acquisition.cancel()
    # The connect call is still in flight; let it finish only after the caller was cancelled.
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await acquisition
    await asyncio.wait_for(closed.wait(), 5)


async def test_connector_requires_state_before_acquiring_a_client(native):
    source, engine, _ = native
    with pytest.raises(EnvironmentProviderError) as missing:
        DOCKER.execution_connector(source.config, runtime=source.runtime, environment_id=source.environment_id)
    assert missing.value.code == "provider_state_required"
    assert engine.client.mock_calls == []


@pytest.mark.parametrize("policy", ["never", "if_missing"])
async def test_image_policy_controls_missing_image_pull(native, policy):
    env, engine, _ = native
    config = env.config.model_copy(update={"pull_policy": policy})
    candidate = DockerTarget(config, "env_test", None, env.runtime)
    assert candidate.fingerprint == env.fingerprint
    engine.client.images.get.side_effect = ImageNotFound("missing")
    try:
        if policy == "never":
            with pytest.raises(EnvironmentProviderError) as raised:
                await candidate.create()
            assert raised.value.code == "environment_image_missing"
            assert "make image-sandbox" in str(raised.value)
            engine.client.images.pull.assert_not_called()
            engine.client.containers.create.assert_not_called()
        else:
            await candidate.create()
            engine.client.images.pull.assert_called_once_with(config.image)
    finally:
        await candidate.close()


@pytest.mark.parametrize(
    "configured,label,expected", [(None, "sandbox", "sandbox"), ("0", "sandbox", "0"), (None, None, None)]
)
async def test_native_execution_user_prefers_recipe_then_image_label(native, configured, label, expected):
    original, engine, container = native
    engine.client.images.get.return_value.labels = {} if label is None else {"ai.a13n.environment.user": label}
    env = DockerTarget(
        original.config.model_copy(update={"user": configured}),
        "env_identity",
        None,
        original.runtime,
    )
    container.labels = env.labels
    await env.create()
    try:
        assert engine.client.containers.create.call_args.kwargs["user"] == expected
    finally:
        await env.close()

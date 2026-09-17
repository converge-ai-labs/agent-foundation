"""Native Docker lifecycle contracts without an Envd dependency."""

from unittest.mock import AsyncMock, Mock

import pytest
from a13n_environment import (
    DockerEnvironment,
    DockerProviderConfiguration,
    DockerProviderRuntime,
    DockerSDKEngine,
    EnvironmentError,
    EnvironmentProviderError,
)
from a13n_environment.docker.commands import DockerCommands
from a13n_environment.docker.configuration import DockerMountConfiguration
from docker.errors import NotFound
from requests.exceptions import ConnectionError

pytestmark = pytest.mark.anyio


@pytest.fixture
def native(monkeypatch):
    engine = DockerSDKEngine(Mock())
    engine.client.containers.get.side_effect = NotFound("missing")
    container = Mock(id="a" * 64, status="created")
    engine.client.containers.create.return_value = container
    monkeypatch.setattr(DockerCommands, "execute", AsyncMock(return_value=b""))
    config = DockerProviderConfiguration(image="python:3.13-slim", pull_policy="never")
    env = DockerEnvironment(config, "env_test", None, DockerProviderRuntime(engine))
    container.labels = env.labels
    return env, engine, container


async def test_create_native_container_records_state_and_overrides_entrypoint(native):
    env, engine, container = native
    await env.prepare()
    try:
        options = engine.client.containers.create.call_args.kwargs
        assert options["init"] is True and options["working_dir"] == "/workspace"
        assert options["entrypoint"] == ["python3", "-I", "-c"]
        assert options["network_mode"] == "bridge"
        assert "ports" not in options and "volumes" not in options
        assert env.dump_state().state["container_id"] == container.id
        assert env.descriptor.backing_identity == container.id
    finally:
        await env.close()
    container.remove.assert_not_called()
    container.stop.assert_not_called()


async def test_start_failure_preserves_allocated_identity(native):
    env, _, container = native
    container.start.side_effect = ConnectionError("lost response")
    with pytest.raises(EnvironmentProviderError) as error:
        await env.prepare()
    assert error.value.certainty == "unknown"
    assert env.dump_state().state["container_id"] == container.id
    await env.close()


async def test_reentry_reuses_container_without_replaying_initialization(native):
    env, engine, container = native
    await env.prepare()
    await env.close()
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    fresh = DockerEnvironment(env.config, env.environment_id, env.dump_state(), env.runtime)
    DockerCommands.execute.reset_mock()
    await fresh.prepare()
    assert DockerCommands.execute.await_count == 1
    assert "Initialization incomplete" in DockerCommands.execute.call_args.args[0][-1]
    assert engine.client.containers.create.call_count == 1
    await fresh.close()


async def test_confirmed_absence_rebuilds_new_generation(native):
    env, engine, container = native
    await env.prepare()
    await env.close()
    state = env.dump_state()
    container.id = "b" * 64
    fresh = DockerEnvironment(env.config, env.environment_id, state, env.runtime)
    await fresh.prepare()
    assert fresh.descriptor.generation != state.state["container_id"]
    assert engine.client.containers.create.call_count == 2
    await fresh.close()


async def test_connection_failure_never_creates_speculative_replacement(native):
    env, engine, _ = native
    engine.client.containers.get.side_effect = ConnectionError("unreachable")
    with pytest.raises(EnvironmentProviderError):
        await env.prepare()
    engine.client.containers.create.assert_not_called()


async def test_foreign_container_cannot_be_adopted_or_destroyed(native):
    env, engine, container = native
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    container.labels = {"a13n.environment": "env_other"}
    with pytest.raises(EnvironmentError, match="another configuration"):
        await env.prepare()
    with pytest.raises(EnvironmentError):
        await env.destroy()
    container.start.assert_not_called()
    container.remove.assert_not_called()


async def test_reconcile_recovers_lost_create_response_without_starting(native):
    env, engine, container = native
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    assert await env.reconcile() == "stopped"
    assert env.dump_state().state["container_id"] == container.id
    container.start.assert_not_called()
    engine.client.containers.create.assert_not_called()


async def test_external_registration_keeps_allocation_identity(native):
    env, engine, container = native
    await env.prepare()
    await env.close()
    container.status = "running"
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    external = DockerEnvironment(
        env.config, "env_registered", env.dump_state(), DockerProviderRuntime(engine, managed=False)
    )
    await external.prepare()
    assert external.environment_id == "env_registered"
    assert external.dump_state().state["environment_id"] == env.environment_id
    await external.close()


async def test_unknown_remove_outcome_preserves_state(native):
    env, engine, container = native
    await env.prepare()
    await env.close()
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    container.remove.side_effect = ConnectionError("lost response")
    fresh = DockerEnvironment(env.config, env.environment_id, env.dump_state(), env.runtime)
    with pytest.raises(EnvironmentProviderError) as error:
        await fresh.destroy()
    assert error.value.certainty == "unknown"
    assert fresh.dump_state() == env.dump_state()


@pytest.mark.parametrize("target", ["/", "/workspace", "/tmp", "/tmp/a13n", "/tmp/a13n/nested"])
def test_mounts_cannot_replace_private_storage(target):
    with pytest.raises(ValueError):
        DockerMountConfiguration(source="/data", target=target)


def test_configuration_excludes_old_envd_and_named_volume_options():
    for values in (
        {"root_mount_id": "root"},
        {"bootstrap": {}},
        {"mounts": [{"source": {"kind": "volume", "name": "x"}, "target": "/data"}]},
    ):
        with pytest.raises(ValueError):
            DockerProviderConfiguration.model_validate(values)


async def test_missing_external_target_never_adopts_same_name_replacement(native):
    env, engine, _ = native
    await env.prepare()
    await env.close()
    engine.client.containers.get.reset_mock()
    external = DockerEnvironment(
        env.config, "env_registered", env.dump_state(), DockerProviderRuntime(engine, managed=False)
    )
    assert await external.reconcile() == "absent"
    engine.client.containers.get.assert_called_once_with(env.dump_state().state["container_id"])
    with pytest.raises(EnvironmentProviderError) as error:
        await external.prepare()
    assert error.value.category == "missing"
    assert engine.client.containers.create.call_count == 1
    await external.close()

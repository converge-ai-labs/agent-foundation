"""Native Docker lifecycle contracts without an Envd dependency."""

import asyncio
import math
import os
import threading
from unittest.mock import AsyncMock, Mock, call

import pytest
from a13n_harness.providers.environment.docker import image_test as image_test_module
from a13n_harness.providers.environment.docker.commands import DockerCommands
from a13n_harness.providers.environment.docker.configuration import (
    DockerEnvironmentConfiguration,
    DockerMountConfiguration,
)
from a13n_harness.providers.environment.docker.processes import DockerProcesses
from a13n_harness.providers.environment.docker.provider import DOCKER, DockerEnvironment, resolve_image
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.models import EnvironmentError
from docker.errors import ImageNotFound, NotFound
from requests.exceptions import ConnectionError

pytestmark = pytest.mark.anyio


@pytest.fixture
def native(monkeypatch):
    engine = DockerSDKEngine(Mock())
    engine.client.containers.get.side_effect = NotFound("missing")
    container = Mock(id="a" * 64, status="created")
    engine.client.containers.create.return_value = container
    engine.client.images.get.return_value.id = "sha256:" + "f" * 64
    engine.client.images.pull.return_value.id = "sha256:" + "e" * 64
    monkeypatch.setattr(DockerCommands, "execute", AsyncMock(return_value=b""))
    config = DockerEnvironmentConfiguration(image="python:3.13-slim", memory_gb=0.25)
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
        assert options["mem_limit"] == 250_000_000
        engine.client.images.get.assert_called_once_with("python:3.13-slim")
        assert engine.client.containers.create.call_args.args[0] == "sha256:" + "f" * 64
        engine.client.images.pull.assert_not_called()
        assert "ports" not in options and "volumes" not in options
        assert env.dump_state().state["container_id"] == container.id
        assert env.descriptor.backing_identity == container.id
    finally:
        await env.close()
    container.remove.assert_not_called()
    container.stop.assert_not_called()


async def test_unused_docker_mount_enters_and_closes_without_engine_io(native):
    from a13n_harness import RunBindings
    from a13n_harness.environment.advanced import create_environment_runtime

    env, engine, _ = native
    runtime = create_environment_runtime(mounts={"workspace": env}, default_mount="workspace")
    async with runtime.bind(
        thread_id="thread-1", run_id="run-1", instance=RunBindings.embedded().instance, host_refs={}
    ) as bound:
        assert bound.snapshot.mounts[0].descriptor.generation == "unprepared"
        assert env.availability.status == "preparing"
        assert env.operations.files is None
        assert engine.client.mock_calls == []
    assert env.availability.status == "unavailable"
    assert engine.client.mock_calls == []
    with pytest.raises(RuntimeError, match="closed"):
        await env.prepare()


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("eager", [False, True])
async def test_docker_aggregate_prepares_once_and_preserves_target(native, monkeypatch, restored, eager):
    from a13n_harness import RunBindings
    from a13n_harness.environment.advanced import create_environment_runtime
    from a13n_harness.providers.environment._guest_files import GuestFiles
    from a13n_harness.providers.environment.files import FileMetadata
    from a13n_harness.providers.environment.models import EnvironmentState

    env, engine, container = native
    if restored:
        env = DockerEnvironment(
            env.config,
            env.environment_id,
            EnvironmentState(
                provider_key="docker",
                state_version="1",
                state={
                    "environment_id": env.environment_id,
                    "container_id": container.id,
                    "configuration_fingerprint": env.fingerprint,
                },
            ),
            env.runtime,
        )
    container.status = "running"

    def lookup(_selector):
        if restored or engine.client.containers.create.called:
            return container
        raise NotFound("missing")

    engine.client.containers.get.side_effect = lookup
    stat = AsyncMock(return_value=FileMetadata(path="/workspace/note.txt", kind="file", size=4, writable=True))
    monkeypatch.setattr(GuestFiles, "stat", stat)
    if eager:
        await env.prepare()
    before_entry = list(engine.client.mock_calls)
    runtime = create_environment_runtime(mounts={"workspace": env}, default_mount="workspace")
    async with runtime.bind(
        thread_id="thread-1", run_id="run-1", instance=RunBindings.embedded().instance, host_refs={}
    ) as bound:
        assert engine.client.mock_calls == before_entry
        for _ in range(2):
            assert (await bound.files.stat("note.txt")).size == 4
        assert env.availability.status == "available"
        assert engine.client.containers.create.call_count == (0 if restored else 1)
        assert stat.await_count == 2
    assert env.availability.status == "unavailable"
    container.remove.assert_not_called()
    container.stop.assert_not_called()


async def test_missing_image_pulls_once(native):
    env, engine, _ = native
    engine.client.images.get.side_effect = ImageNotFound("absent")
    await env.prepare()
    engine.client.images.pull.assert_called_once_with(env.config.image)
    assert engine.client.containers.create.call_args.args[0] == "sha256:" + "e" * 64
    await env.close()


async def test_image_test_pins_reported_identity_when_tag_changes(native, monkeypatch):
    env, engine, _ = native
    image_a = Mock(id="sha256:" + "a" * 64)
    image_b = Mock(id="sha256:" + "b" * 64)
    images = {env.config.image: image_a, image_a.id: image_a}
    engine.client.images.get.side_effect = lambda ref: images[ref]
    engine.client.ping.return_value = True

    def retag_after_resolve(client, ref):
        resolved = resolve_image(client, ref)
        images[ref] = image_b
        return resolved

    async def exercise(_engine, configuration, image_id):
        pinned = DockerEnvironment(
            configuration.model_copy(update={"image": image_id}), "env_pinned", None, env.runtime
        )
        await asyncio.to_thread(pinned._create)
        return ("pinned",)

    monkeypatch.setattr(image_test_module, "resolve_image", retag_after_resolve)
    monkeypatch.setattr(image_test_module, "_exercise_image", exercise)
    result = await image_test_module.test_docker_image(engine, env.config)
    assert result.image_id == image_a.id
    assert engine.client.containers.create.call_args.args[0] == image_a.id
    assert engine.client.images.get.call_args_list == [call(env.config.image), call(image_a.id)]
    engine.client.images.pull.assert_not_called()


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
    env = DockerEnvironment(
        DockerEnvironmentConfiguration(image=env.config.image, memory_gb=0.006291456),
        env.environment_id,
        None,
        env.runtime,
    )
    await env.prepare()
    assert engine.client.containers.create.call_args.kwargs["mem_limit"] == 6_291_456
    await env.close()


async def test_preparation_diagnostic_preserves_non_permission_error(native):
    env, engine, container = native
    env.target = Mock(container_id="container-id")
    container.status = "running"
    container.exec_run.return_value.exit_code = 0
    engine.client.containers.get.side_effect = None
    engine.client.containers.get.return_value = container
    message = await image_test_module._preparation_failure(
        engine, env, env.config, RuntimeError("architecture mismatch")
    )
    assert "architecture mismatch" in message
    assert "not writable" not in message


async def test_image_test_rejects_process_that_exits_before_kill(monkeypatch):
    image = os.environ.get("A13N_TEST_DOCKER_IMAGE")
    if not image:
        pytest.skip("Set A13N_TEST_DOCKER_IMAGE for real process-control coverage")
    engine = DockerSDKEngine.connect("unix:///var/run/docker.sock")
    original_start = DockerProcesses.start

    async def exit_early(self, request):
        from a13n_harness.providers.environment.commands import ArgvCommand

        if isinstance(request.command, ArgvCommand) and "time.sleep" in request.command.arguments[-1]:
            request = request.model_copy(
                update={
                    "command": ArgvCommand(
                        executable=request.command.executable,
                        arguments=("-I", "-c", "raise SystemExit(127)"),
                    )
                }
            )
            result = await original_start(self, request)
            await self.wait(result.process.handle, condition="initial_terminal", timeout_seconds=5)
            return result
        return await original_start(self, request)

    monkeypatch.setattr(DockerProcesses, "start", exit_early)
    try:
        with pytest.raises(image_test_module.DockerImageTestFailure, match="exited before control check"):
            await image_test_module.test_docker_image(engine, DockerEnvironmentConfiguration(image=image))
    finally:
        await engine.close()


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
    task = asyncio.create_task(env.prepare())
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
        env.config, "env_registered", env.dump_state(), DockerProviderRuntime(engine), allow_create=False
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
        {"pull_policy": "never"},
        {"memory_mib": 256},
        {"bootstrap": {}},
        {"mounts": [{"source": {"kind": "volume", "name": "x"}, "target": "/data"}]},
    ):
        with pytest.raises(ValueError):
            DockerEnvironmentConfiguration.model_validate(values)


async def test_missing_external_target_never_adopts_same_name_replacement(native):
    env, engine, _ = native
    await env.prepare()
    await env.close()
    engine.client.containers.get.reset_mock()
    external = DockerEnvironment(
        env.config, "env_registered", env.dump_state(), DockerProviderRuntime(engine), allow_create=False
    )
    assert await external.reconcile() == "absent"
    engine.client.containers.get.assert_called_once_with(env.dump_state().state["container_id"])
    with pytest.raises(EnvironmentProviderError) as error:
        await external.prepare()
    assert error.value.category == "missing"
    assert engine.client.containers.create.call_count == 1
    await external.close()


async def test_cancelled_engine_acquisition_closes_the_engine_it_owns(monkeypatch):
    """A cancelled caller must never leave an acquired Docker client open."""
    acquiring, closed = threading.Event(), asyncio.Event()
    engine = DockerSDKEngine(Mock())

    def connect(docker_host, *, timeout_seconds=60):
        del docker_host, timeout_seconds
        acquiring.set()
        threading.Event().wait(0.2)
        return engine

    def close() -> None:
        closed.set()

    engine.client.close.side_effect = close
    monkeypatch.setattr(DockerSDKEngine, "connect", connect)
    acquisition = asyncio.create_task(DOCKER.create({}, configuration={"docker_host": "unix:///var/run/docker.sock"}))
    await asyncio.to_thread(acquiring.wait, 5)
    acquisition.cancel()
    with pytest.raises(asyncio.CancelledError):
        await acquisition
    await asyncio.wait_for(closed.wait(), 5)


async def test_borrowed_docker_runtime_obeys_each_adapters_creation_policy(native):
    source, engine, _ = native
    external = await DOCKER.create(
        source.config,
        runtime=source.runtime,
        environment_id=source.environment_id,
        allow_create=False,
    )
    with pytest.raises(EnvironmentProviderError) as error:
        await external.prepare()
    assert error.value.category == "missing"
    engine.client.containers.create.assert_not_called()
    await external.close()
    engine.client.close.assert_not_called()

    managed = await DOCKER.create(
        source.config,
        runtime=source.runtime,
        environment_id=source.environment_id,
        allow_create=True,
    )
    await managed.prepare()
    engine.client.containers.create.assert_called_once()
    await managed.close()
    engine.client.close.assert_not_called()

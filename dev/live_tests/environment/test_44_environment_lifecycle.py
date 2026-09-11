"""Shared scope boundaries and Docker's durable target semantics on real backends."""

import asyncio
from dataclasses import replace

import pytest
from a13n_environment import EnvironmentError, EnvironmentProviderError
from a13n_environment.docker.runtime import DockerEngineError

from .file_backends import KINDS, FileBackend

pytestmark = pytest.mark.anyio


@pytest.fixture(params=KINDS)
async def backend(request, tmp_path):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in with --live-environments for real provider lifecycle boundaries")
    async with FileBackend(request.param, tmp_path).open() as target:
        yield target


async def assert_closed_files(files):
    for call in (
        lambda: files.stat("/file-tests/source"),
        lambda: files.read_bytes("/file-tests/source"),
        lambda: files.write_text("/file-tests/source", "CLOSED_WRITE", mode="replace"),
    ):
        with pytest.raises(EnvironmentError) as error:
            await call()
        assert error.value.code in {"environment_unavailable", "environment_stale_mount"}


async def test_close_fences_file_facets_and_fresh_scope_preserves_workspace(backend):
    environment = backend.environment
    original_generation = environment.descriptor.generation
    original_backing = environment.descriptor.backing_identity
    state = environment.dump_state()
    old_files = environment.operations.files
    before = backend.snapshot()
    await environment.close()
    await environment.close()
    await assert_closed_files(old_files)
    assert backend.snapshot() == before
    if backend.process is not None:
        assert backend.process.returncode is None, "Closing a remote adapter stopped its external daemon"
    fresh = backend.adapter(state=state)
    await backend.prepare(fresh)
    assert await fresh.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"
    if backend.kind in {"direct-local", "local-envd"}:
        assert fresh.dump_state() is None
        assert fresh.descriptor.generation != original_generation
        assert fresh.descriptor.backing_identity == original_backing
    else:
        assert fresh.dump_state() == state
        assert fresh.descriptor.generation == original_generation


@pytest.mark.parametrize("cancel", [False, True], ids=["complete", "cancel"])
async def test_close_serializes_with_real_preparation(backend, monkeypatch, cancel):
    state = backend.environment.dump_state()
    await backend.environment.close()
    environment = backend.adapter(state=state)
    await environment.enter(thread_id="files", run_id="racing-run", agent_instance_id="agent", mount_id="workspace")
    opened, release = asyncio.Event(), asyncio.Event()
    prepare_native = environment._prepare

    async def paused_publication(**arguments):
        await prepare_native(**arguments)
        opened.set()
        await release.wait()

    monkeypatch.setattr(environment, "_prepare", paused_publication)
    prepare = asyncio.create_task(environment.prepare())
    close = None
    try:
        async with asyncio.timeout(20):
            await opened.wait()
        files = environment.operations.files
        close = asyncio.create_task(environment.close())
        await asyncio.sleep(0)
        assert not close.done(), "Close escaped the preparation barrier"
        if cancel:
            prepare.cancel()
            with pytest.raises(asyncio.CancelledError):
                await prepare
        else:
            release.set()
            await prepare
        await asyncio.wait_for(close, 20)
        await assert_closed_files(files)
        assert (backend.root / "file-tests/source").read_text() == "ORIGINAL\n"
    finally:
        release.set()
        if not prepare.done():
            prepare.cancel()
        await asyncio.gather(prepare, *([close] if close is not None else []), return_exceptions=True)


@pytest.fixture
async def docker_backend(request, tmp_path):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in with --live-environments for real Docker lifecycle faults")
    async with FileBackend("docker", tmp_path).open() as target:
        yield target


async def test_docker_stop_resume_and_metadata_recovery_preserve_only_target_identity(docker_backend, monkeypatch):
    backend = docker_backend
    state = backend.environment.dump_state()
    await backend.environment.close()
    stopped = backend.adapter(state=state)
    await stopped.stop()
    assert await stopped.reconcile() == "stopped"
    original_id = state.state["container_id"]
    native = await backend.engine.inspect_container(original_id)
    assert native is not None and native.status != "running"
    discovered = backend.adapter()

    async def no_create(_spec):
        raise AssertionError("Metadata recovery created a duplicate container")

    monkeypatch.setattr(backend.engine, "create_container", no_create)
    assert await discovered.reconcile() == "stopped"
    assert discovered.dump_state().state["container_id"] == original_id
    assert (await backend.engine.inspect_container(original_id)).status != "running", "Observation started the target"
    resumed = await backend.prepare(backend.adapter(state=discovered.dump_state()))
    assert resumed.dump_state().state["container_id"] == original_id
    assert await resumed.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"
    # Docker stop/start does not promise preservation of process memory.


@pytest.mark.parametrize("managed", [True, False], ids=["managed", "external"])
async def test_docker_external_removal_rebuilds_only_managed_targets(docker_backend, managed):
    backend = docker_backend
    state = backend.environment.dump_state()
    await backend.environment.close()
    old_id = state.state["container_id"]
    await backend.engine.stop_container(old_id, timeout_seconds=1)
    await backend.engine.remove_container(old_id)
    fresh = backend.adapter(state=state, runtime=replace(backend.runtime, managed=managed))
    if managed:
        await backend.prepare(fresh)
        assert fresh.dump_state().state["container_id"] != old_id
        assert await fresh.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"
        backend.environment = fresh
    else:
        with pytest.raises(EnvironmentProviderError):
            await backend.prepare(fresh)
        assert fresh.dump_state() == state
        assert fresh.operations.files is None
    assert await backend.engine.inspect_container(old_id) is None


async def test_docker_lost_create_response_recovers_one_owned_container(docker_backend, monkeypatch):
    backend = docker_backend
    state = backend.environment.dump_state()
    await backend.environment.close()
    await backend.adapter(state=state).destroy()
    native_create = backend.engine.create_container
    created = []

    async def lost_response(spec):
        container_id = await native_create(spec)
        created.append(container_id)
        raise DockerEngineError("Injected lost create response", dispatched=True)

    monkeypatch.setattr(backend.engine, "create_container", lost_response)
    failed = backend.adapter()
    with pytest.raises(EnvironmentProviderError):
        await backend.prepare(failed)
    assert len(created) == 1
    await failed.close()
    recovered = backend.adapter()
    assert await recovered.reconcile() == "stopped"
    assert recovered.dump_state().state["container_id"] == created[0]
    await backend.prepare(recovered)
    assert len(created) == 1
    assert await recovered.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"
    backend.environment = recovered


@pytest.mark.parametrize("action", ["stop", "destroy"])
async def test_docker_lost_mutation_response_is_reconciled_without_repeating_effect(
    docker_backend, monkeypatch, action
):
    backend = docker_backend
    state = backend.environment.dump_state()
    await backend.environment.close()
    target = backend.adapter(state=state)
    method = "stop_container" if action == "stop" else "remove_container"
    native = getattr(backend.engine, method)
    effects = []

    async def lost_response(*arguments, **keywords):
        await native(*arguments, **keywords)
        effects.append(arguments[0])
        raise DockerEngineError("Injected lost mutation response", dispatched=True)

    monkeypatch.setattr(backend.engine, method, lost_response)
    with pytest.raises(EnvironmentProviderError):
        await getattr(target, action)()
    assert target.dump_state() == state
    assert len(effects) == 1
    assert await target.reconcile() == ("stopped" if action == "stop" else "absent")
    retry = backend.adapter(state=target.dump_state()) if action == "destroy" else target
    await getattr(retry, action)()
    assert len(effects) == 1

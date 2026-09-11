"""Lifecycle gaps that require real local processes or exact Docker targets."""

import asyncio
import logging
import os
import signal

import pytest
from a13n_environment import EnvironmentError, EnvironmentProviderError
from a13n_environment.commands import CommandRequest, ShellCommand
from a13n_environment.retention import EnvironmentOutputPolicy

from .e2b_support import eventually
from .file_backends import FileBackend

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)
KINDS = ("direct-local", "local-envd", "docker")
OUTPUT = EnvironmentOutputPolicy(max_inline_bytes=8, max_output_bytes=4096, overflow="retain")


@pytest.fixture(autouse=True)
def live_opt_in(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for real Local and Docker lifecycle boundaries")


def request(script, *, stdin=False):
    return CommandRequest(
        command=ShellCommand(profile_id="default", script=script),
        cwd="/",
        keep_stdin_open=stdin,
        output_policy=OUTPUT,
    )


async def targets(backend):
    return await backend.engine.find_containers(
        {"io.a13n.environment-provider": "a13n.docker", "io.a13n.environment-id": backend.identity}
    )


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.parametrize("kind", KINDS)
async def test_inert_entry_unused_close_and_concurrent_prepare_once(tmp_path, monkeypatch, kind):
    async with FileBackend(kind, tmp_path).open(prepare=False) as backend:
        before = set(tmp_path.rglob("*"))
        unused = backend.environment
        await unused.enter(thread_id="t", run_id="unused", agent_instance_id="a", mount_id="workspace")
        assert unused.operations.files is None and unused.dump_state() is None
        await unused.close()
        assert set(tmp_path.rglob("*")) == before
        if kind == "docker":
            assert await targets(backend) == ()
        environment = backend.adapter()
        backend.environment = environment
        await environment.enter(thread_id="t", run_id="parallel", agent_instance_id="a", mount_id="workspace")
        native, calls = environment._prepare, []

        async def prepare(**arguments):
            calls.append(1)
            await native(**arguments)

        monkeypatch.setattr(environment, "_prepare", prepare)
        await asyncio.gather(*(environment.prepare() for _ in range(5)))
        await environment.prepare()
        assert calls == [1]
        assert await environment.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"
        if kind == "docker":
            assert len(await targets(backend)) == 1
        logger.info("Inert entry and concurrent preparation verified backend=%s native_calls=1", kind)


@pytest.mark.parametrize("kind", ["direct-local", "local-envd"])
async def test_local_missing_root_is_not_created_by_prepare(tmp_path, kind):
    async with FileBackend(kind, tmp_path).open(prepare=False) as backend:
        saved = tmp_path / "saved-workspace"
        backend.root.rename(saved)
        with pytest.raises(EnvironmentError if kind == "direct-local" else EnvironmentProviderError):
            await backend.prepare(backend.environment)
        assert not backend.root.exists()
        assert (saved / "file-tests/source").read_bytes() == b"ORIGINAL\n"
        assert backend.environment.dump_state() is None
        assert backend.environment.operations.files is None


@pytest.mark.parametrize("kind", ["direct-local", "local-envd"])
async def test_replacing_local_root_changes_backing_identity(tmp_path, kind):
    async with FileBackend(kind, tmp_path).open() as backend:
        original = backend.environment.descriptor
        await backend.environment.close()
        backend.root.rename(tmp_path / "old-root")
        backend.root.mkdir()
        (backend.root / "replacement").write_text("NEW_ROOT")
        fresh = await backend.prepare(backend.adapter())
        assert fresh.descriptor.backing_identity != original.backing_identity
        assert fresh.descriptor.generation != original.generation
        assert await fresh.operations.files.read_bytes("/replacement") == b"NEW_ROOT"
        assert (tmp_path / "old-root/file-tests/source").read_bytes() == b"ORIGINAL\n"


@pytest.mark.parametrize("kind", ["direct-local", "local-envd"])
async def test_local_close_terminates_process_tree_and_fences_output(tmp_path, kind):
    async with FileBackend(kind, tmp_path, commands=True).open() as backend:
        environment = backend.environment
        processes, outputs = environment.operations.processes, environment.operations.outputs
        retained = await processes.start(request("printf 'PREFIX_0123456789'"))
        await processes.wait(retained.process.handle, condition="tree_cleaned", timeout_seconds=10)
        observed = await processes.read_output(retained.process.handle, stdout_start_offset=0, policy=OUTPUT)
        reference = observed.stdout.capture.reference
        assert reference is not None
        started = await processes.start(
            request("printf '%s' $$ > owner.pid; sleep 120 & printf '%s' $! > child.pid; wait", stdin=True)
        )
        await eventually(lambda: asyncio.to_thread((backend.root / "child.pid").exists), bool, "Child started")
        pids = [int((backend.root / name).read_text()) for name in ("owner.pid", "child.pid")]
        assert all(alive(pid) for pid in pids)
        daemon = environment._process if kind == "local-envd" else None
        await environment.close()
        await eventually(
            lambda: asyncio.to_thread(lambda: all(not alive(pid) for pid in pids)), bool, "Owned tree exited"
        )
        if daemon is not None:
            assert daemon.returncode is not None
        for operation in (
            lambda: processes.inspect(started.process.handle),
            lambda: outputs.read(reference, start_offset=0, policy=OUTPUT),
        ):
            with pytest.raises(EnvironmentError):
                await operation()
        fresh = await backend.prepare(backend.adapter())
        with pytest.raises(EnvironmentError):
            await fresh.operations.processes.inspect(started.process.handle)
        with pytest.raises(EnvironmentError):
            await fresh.operations.outputs.read(reference, start_offset=0, policy=OUTPUT)
        assert await fresh.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"
        logger.info("Local close removed owned PIDs and fenced process/output handles backend=%s", kind)


@pytest.mark.parametrize("cancel", [False, True], ids=["error", "cancel"])
async def test_local_envd_failure_after_launch_cleans_private_generation(tmp_path, monkeypatch, cancel):
    async with FileBackend("local-envd", tmp_path).open(prepare=False) as backend:
        environment = backend.environment
        native = environment._launch_private_generation
        launched, release = asyncio.Event(), asyncio.Event()
        evidence = {}
        before = set(tmp_path.iterdir())

        async def launch():
            await native()
            evidence["process"] = environment._process
            launched.set()
            if cancel:
                await release.wait()
            raise RuntimeError("Injected failure after real daemon launch")

        monkeypatch.setattr(environment, "_launch_private_generation", launch)
        task = asyncio.create_task(backend.prepare(environment))
        await asyncio.wait_for(launched.wait(), 20)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else EnvironmentProviderError):
            await task
        assert evidence["process"].returncode is not None
        assert set(tmp_path.iterdir()) == before
        assert environment.operations.files is None and environment.dump_state() is None
        await environment.close()
        fresh = await backend.prepare(backend.adapter())
        assert await fresh.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"


@pytest.mark.parametrize("cancel", [False, True], ids=["readiness-error", "cancel"])
async def test_docker_failure_after_create_preserves_target_for_fresh_scope(tmp_path, monkeypatch, cancel):
    async with FileBackend("docker", tmp_path).open(prepare=False) as backend:
        environment = backend.environment
        reached, release = asyncio.Event(), asyncio.Event()

        async def unavailable(*args, **kwargs):
            reached.set()
            if cancel:
                await release.wait()
            raise EnvironmentError("Injected readiness failure", code="environment_unavailable")

        monkeypatch.setattr(environment, "_open_eip", unavailable)
        task = asyncio.create_task(backend.prepare(environment))
        await asyncio.wait_for(reached.wait(), 30)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else EnvironmentError):
            await task
        state = environment.dump_state()
        assert state is not None and environment.operations.files is None
        identity = state.state["container_id"]
        assert (await backend.engine.inspect_container(identity)).status == "running"
        await environment.close()
        fresh = await backend.prepare(backend.adapter(state=state))
        assert fresh.dump_state() == state
        assert [item.container_id for item in await targets(backend)] == [identity]
        assert await fresh.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"
        backend.environment = fresh


@pytest.mark.parametrize("action", ["prepare", "stop", "destroy"])
async def test_docker_redirected_state_cannot_mutate_another_target(tmp_path, action):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    async with FileBackend("docker", first).open() as backend, FileBackend("docker", second).open() as bystander:
        state = backend.environment.dump_state()
        other = bystander.environment.dump_state().state["container_id"]
        await backend.environment.close()
        forged = state.model_copy(deep=True)
        forged.state["container_id"] = other
        rejected = backend.adapter(state=forged)
        with pytest.raises(EnvironmentProviderError):
            if action == "prepare":
                await backend.prepare(rejected)
            else:
                await getattr(rejected, action)()
        for identity in (state.state["container_id"], other):
            assert (await backend.engine.inspect_container(identity)).status == "running"
        assert await bystander.environment.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"


@pytest.mark.parametrize("action", ["prepare", "reconcile"])
async def test_docker_ambiguous_real_metadata_never_adopts_or_creates(tmp_path, monkeypatch, action):
    async with FileBackend("docker", tmp_path).open(prepare=False) as backend:
        native, specs = backend.engine.create_container, []

        async def create(spec):
            specs.append(spec)
            return await native(spec)

        monkeypatch.setattr(backend.engine, "create_container", create)
        await backend.prepare(backend.environment)
        await backend.environment.close()
        duplicate = await native(specs[0])
        try:
            before = {item.container_id: item.status for item in await targets(backend)}
            rejected = backend.adapter()
            with pytest.raises(EnvironmentProviderError):
                if action == "prepare":
                    await backend.prepare(rejected)
                else:
                    await rejected.reconcile()
            assert rejected.dump_state() is None
            assert {item.container_id: item.status for item in await targets(backend)} == before
            assert len(specs) == 1
        finally:
            await backend.engine.remove_container(duplicate)


async def test_docker_destroy_is_exact_idempotent_and_preserves_bind_directory(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    async with FileBackend("docker", first).open() as backend, FileBackend("docker", second).open() as bystander:
        state = backend.environment.dump_state()
        await backend.environment.close()
        destroy = backend.adapter(state=state)
        await destroy.destroy()
        await backend.adapter(state=state).destroy()
        assert await backend.engine.inspect_container(state.state["container_id"]) is None
        assert await backend.runtime.bootstrap_store.recover(state.state["bootstrap_correlation"]) is None
        assert (backend.root / "file-tests/source").read_bytes() == b"ORIGINAL\n"
        assert await bystander.environment.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"


@pytest.mark.parametrize("active", [False, True], ids=["idle", "active-process-tree"])
async def test_local_envd_daemon_death_fences_old_scope_and_allows_fresh_generation(tmp_path, active):
    async with FileBackend("local-envd", tmp_path, commands=active).open() as backend:
        environment = backend.environment
        generation = environment.descriptor.generation
        files = environment.operations.files
        process = environment._process
        pids, handle, reference = [], None, None
        if active:
            processes, outputs = environment.operations.processes, environment.operations.outputs
            started = await processes.start(
                request(
                    "printf '%s' $$ > owner.pid; sleep 120 & printf '%s' $! > child.pid; printf 'PREFIX_0123456789'; wait",
                    stdin=True,
                )
            )
            handle = started.process.handle
            observed = await eventually(
                lambda: processes.read_output(handle, stdout_start_offset=0, policy=OUTPUT),
                lambda page: page.stdout.capture.available_end == 17,
                "Live output exists before daemon death",
            )
            reference = observed.stdout.capture.reference
            assert reference is not None
            pids = [int((backend.root / name).read_text()) for name in ("owner.pid", "child.pid")]
            assert all(alive(pid) for pid in pids)
        os.kill(process.pid, signal.SIGKILL)
        await asyncio.wait_for(process.wait(), 10)
        with pytest.raises(EnvironmentError):
            await files.read_bytes("/file-tests/source")
        with pytest.raises(EnvironmentProviderError) as caught:
            await environment.close()
        assert caught.value.code == "provider_cleanup_failed"
        assert not list(tmp_path.glob("a13n-local-envd-*"))
        try:
            await eventually(
                lambda: asyncio.to_thread(lambda: all(not alive(pid) for pid in pids)),
                bool,
                "Orphaned native process tree was cleaned",
                timeout=15,
            )
        finally:
            for pid in pids:
                if alive(pid):
                    os.kill(pid, signal.SIGKILL)
        fresh = await backend.prepare(backend.adapter())
        assert fresh.descriptor.generation != generation
        assert await fresh.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"
        with pytest.raises(EnvironmentError):
            await files.read_bytes("/file-tests/source")
        if active:
            for call in (
                lambda: processes.inspect(handle),
                lambda: outputs.read(reference, start_offset=0, policy=OUTPUT),
                lambda: fresh.operations.processes.rebind(handle.identity, output_policy=OUTPUT),
                lambda: fresh.operations.outputs.read(reference, start_offset=0, policy=OUTPUT),
            ):
                with pytest.raises(EnvironmentError):
                    await call()


@pytest.mark.parametrize("restart", [False, True], ids=["close-rebind", "stop-start-fences"])
async def test_docker_session_close_and_native_restart_have_distinct_process_semantics(tmp_path, restart):
    async with FileBackend("docker", tmp_path, commands=True).open() as backend:
        environment = backend.environment
        processes = environment.operations.processes
        started = await processes.start(request("printf 'PREFIX_0123456789'; read line; printf AFTER", stdin=True))
        handle = started.process.handle
        observed = await eventually(
            lambda: processes.read_output(handle, stdout_start_offset=0, policy=OUTPUT),
            lambda page: page.stdout.capture.available_end == 17,
            "Docker output retained before close",
        )
        reference = observed.stdout.capture.reference
        assert reference is not None
        state, descriptor = environment.dump_state(), environment.descriptor
        await environment.close()
        assert (await backend.engine.inspect_container(state.state["container_id"])).status == "running"
        if restart:
            await backend.adapter(state=state).stop()
            assert (await backend.engine.inspect_container(state.state["container_id"])).status == "exited"
        fresh = await backend.prepare(backend.adapter(state=state))
        backend.environment = fresh
        assert fresh.descriptor.backing_identity == descriptor.backing_identity
        assert (fresh.descriptor.generation != descriptor.generation) == restart
        assert len(await targets(backend)) == 1
        with pytest.raises(EnvironmentError):
            await fresh.operations.processes.inspect(handle)
        if restart:
            with pytest.raises(EnvironmentError):
                await fresh.operations.processes.rebind(handle.identity, output_policy=OUTPUT)
            with pytest.raises(EnvironmentError):
                await fresh.operations.outputs.read(reference, start_offset=0, policy=OUTPUT)
        else:
            rebound = await fresh.operations.processes.rebind(handle.identity, output_policy=OUTPUT)
            assert rebound.status.phase == "running"
            await fresh.operations.processes.write_stdin(rebound.handle, b"continue\n")
            completed = await fresh.operations.processes.wait(
                rebound.handle, condition="initial_terminal", timeout_seconds=20
            )
            assert completed.status.exit_code == 0
            tail = await fresh.operations.processes.read_output(rebound.handle, stdout_start_offset=17, policy=OUTPUT)
            assert b"".join(chunk.data for chunk in tail.stdout.chunks) == b"AFTER"
        assert await fresh.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"
        logger.info(
            "Docker native generation transition restart=%s old=%s new=%s",
            restart,
            descriptor.generation,
            fresh.descriptor.generation,
        )

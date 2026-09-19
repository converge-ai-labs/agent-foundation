"""Lifecycle gaps that require real local processes."""

import asyncio
import logging
import os
import signal

import pytest
from a13n_harness.providers.environment.commands import CommandRequest, ShellCommand
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.models import EnvironmentError
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy

from .e2b_support import eventually
from .file_backends import FileBackend

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)
KINDS = ("direct_local", "local_envd")
OUTPUT = EnvironmentOutputPolicy(max_inline_bytes=8, max_output_bytes=4096, overflow="retain")


@pytest.fixture(autouse=True)
def live_opt_in(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for real local lifecycle boundaries")


def request(script, *, stdin=False):
    return CommandRequest(
        command=ShellCommand(profile_id="default", script=script),
        cwd="/",
        keep_stdin_open=stdin,
        output_policy=OUTPUT,
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
        await unused.enter(mount_id="workspace")
        assert unused.operations.files is None and unused.dump_state() is None
        await unused.close()
        assert set(tmp_path.rglob("*")) == before
        environment = backend.adapter()
        backend.environment = environment
        await environment.enter(mount_id="workspace")
        native, calls = environment._prepare, []

        async def prepare(**arguments):
            calls.append(1)
            await native(**arguments)

        monkeypatch.setattr(environment, "_prepare", prepare)
        await asyncio.gather(*(environment.prepare() for _ in range(5)))
        await environment.prepare()
        assert calls == [1]
        assert await environment.operations.files.read_bytes("/file-tests/source") == b"ORIGINAL\n"
        logger.info("Inert entry and concurrent preparation verified backend=%s native_calls=1", kind)


@pytest.mark.parametrize("kind", ["direct_local", "local_envd"])
async def test_local_missing_root_is_not_created_by_prepare(tmp_path, kind):
    async with FileBackend(kind, tmp_path).open(prepare=False) as backend:
        saved = tmp_path / "saved-workspace"
        backend.root.rename(saved)
        with pytest.raises(EnvironmentError if kind == "direct_local" else EnvironmentProviderError):
            await backend.prepare(backend.environment)
        assert not backend.root.exists()
        assert (saved / "file-tests/source").read_bytes() == b"ORIGINAL\n"
        assert backend.environment.dump_state() is None
        assert backend.environment.operations.files is None


@pytest.mark.parametrize("kind", ["direct_local", "local_envd"])
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


@pytest.mark.parametrize("kind", ["direct_local", "local_envd"])
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
        daemon = environment._process if kind == "local_envd" else None
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
    async with FileBackend("local_envd", tmp_path).open(prepare=False) as backend:
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


@pytest.mark.parametrize("active", [False, True], ids=["idle", "active-process-tree"])
async def test_local_envd_daemon_death_fences_old_scope_and_allows_fresh_generation(tmp_path, active):
    async with FileBackend("local_envd", tmp_path, commands=active).open() as backend:
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
        assert not list(tmp_path.glob("a13n-local_envd-*"))
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

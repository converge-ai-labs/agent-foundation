"""Local and remote Envd scope boundaries on real backends."""

import asyncio

import pytest
from a13n_environment import EnvironmentError

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

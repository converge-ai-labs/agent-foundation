"""Remote process/output continuity and unknown outcomes across real carrier loss."""

import asyncio
import signal

import pytest
from a13n_harness.providers.environment.commands import CommandRequest, ShellCommand
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.models import EnvironmentError
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy

from .e2b_support import eventually
from .file_backends import FileBackend

pytestmark = pytest.mark.anyio
OUTPUT = EnvironmentOutputPolicy(max_inline_bytes=8, max_output_bytes=4096, overflow="retain")


def command(script, *, stdin=False):
    return CommandRequest(
        command=ShellCommand(profile_id="default", script=script), cwd="/", keep_stdin_open=stdin, output_policy=OUTPUT
    )


@pytest.fixture(params=["http_envd", "websocket_envd"])
async def remote(request, tmp_path):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for real remote Envd transport faults")
    async with FileBackend(request.param, tmp_path, commands=True, network_faults=True).open() as backend:
        yield backend


async def running_process(backend):
    processes = backend.environment.operations.processes
    started = await processes.start(command("printf 'PREFIX_0123456789'; read line; printf AFTER", stdin=True))
    page = await eventually(
        lambda: processes.read_output(started.process.handle, stdout_start_offset=0, policy=OUTPUT),
        lambda page: page.stdout.capture.available_end == 17,
        "Remote prefix retained",
    )
    assert page.stdout.capture.reference is not None
    return started.process.handle, page.stdout.capture.reference


async def finish_rebound(environment, identity):
    processes = environment.operations.processes
    rebound = await processes.rebind(identity, output_policy=OUTPUT)
    assert rebound.status.phase == "running"
    await processes.write_stdin(rebound.handle, b"continue\n")
    result = await processes.wait(rebound.handle, condition="tree_cleaned", timeout_seconds=10)
    assert result.status.exit_code == 0
    page = await processes.read_output(rebound.handle, stdout_start_offset=17, policy=OUTPUT)
    assert b"".join(chunk.data for chunk in page.stdout.chunks) == b"AFTER"
    assert page.stdout.chunks[0].start_offset == 17


async def test_clean_close_rebind_preserves_native_process_stdin_and_output_offsets(remote):
    backend = remote
    original = backend.environment
    outputs = original.operations.outputs
    handle, reference = await running_process(backend)
    await original.close()
    assert backend.process.returncode is None
    fresh = await backend.prepare(backend.adapter())
    assert fresh.descriptor.generation == original.descriptor.generation
    with pytest.raises(EnvironmentError):
        await fresh.operations.processes.inspect(handle)
    await finish_rebound(fresh, handle.identity)
    with pytest.raises(EnvironmentError):
        await outputs.read(reference, start_offset=0, policy=OUTPUT)


async def test_external_daemon_restart_fences_process_and_output_without_replaying(remote):
    backend = remote
    original = backend.environment
    processes, outputs = original.operations.processes, original.operations.outputs
    handle, reference = await running_process(backend)
    (backend.root / "sentinel").write_text("BEFORE_RESTART")
    backend.process.send_signal(signal.SIGKILL)
    await asyncio.wait_for(backend.process.wait(), 10)
    with pytest.raises(EnvironmentError):
        await processes.inspect(handle)
    try:
        await original.close()
    except (EnvironmentError, EnvironmentProviderError):
        pass  # Abrupt loss cannot acknowledge clean EIP closure.
    await backend.launch_daemon()
    fresh = await backend.prepare(backend.adapter())
    assert fresh.descriptor.generation != original.descriptor.generation
    for call in (
        lambda: processes.inspect(handle),
        lambda: outputs.read(reference, start_offset=0, policy=OUTPUT),
        lambda: fresh.operations.processes.rebind(handle.identity, output_policy=OUTPUT),
        lambda: fresh.operations.outputs.read(reference, start_offset=0, policy=OUTPUT),
    ):
        with pytest.raises(EnvironmentError):
            await call()
    assert await fresh.operations.files.read_bytes("/sentinel") == b"BEFORE_RESTART"
    assert {path.name for path in backend.root.iterdir()} == {"file-tests", "sentinel"}


async def test_real_connection_loss_does_not_replay_dispatched_command(remote):
    backend = remote
    original = backend.environment
    handle, _ = await running_process(backend)
    effect = backend.root / "effect"
    # The operation changes native state before its response can reach the Host.
    operation = asyncio.create_task(
        original.operations.shell.exec(command("printf ONCE >> effect; sleep 3; printf DONE"))
    )
    await eventually(lambda: asyncio.to_thread(effect.exists), bool, "Native command changed state before response")
    backend.proxy.cut()
    try:
        with pytest.raises(EnvironmentError):
            await asyncio.wait_for(operation, 15)
        assert effect.read_bytes() == b"ONCE"
        with pytest.raises(EnvironmentError):
            await original.operations.processes.inspect(handle)
    finally:
        backend.proxy.restore()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
    try:
        await original.close()
    except (EnvironmentError, EnvironmentProviderError):
        pass
    if backend.kind == "http_envd":
        # An abandoned HTTP Session stays admitted; a fresh adapter must not steal it.
        with pytest.raises(EnvironmentProviderError):
            await backend.prepare(backend.adapter())
        assert backend.process.returncode is None
        await backend.stop_daemon()
        await backend.launch_daemon()
    fresh = await backend.prepare(backend.adapter())
    if backend.kind == "websocket_envd":
        assert fresh.descriptor.generation == original.descriptor.generation
        await finish_rebound(fresh, handle.identity)
    else:
        assert fresh.descriptor.generation != original.descriptor.generation
        with pytest.raises(EnvironmentError):
            await fresh.operations.processes.rebind(handle.identity, output_policy=OUTPUT)
    assert await fresh.operations.files.read_bytes("/effect") == b"ONCE"

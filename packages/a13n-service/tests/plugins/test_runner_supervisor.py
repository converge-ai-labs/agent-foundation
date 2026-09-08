import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_service.plugins import runner_supervisor as module
from a13n_service.plugins.commands import PluginRuntimeCommandFailure
from a13n_service.plugins.runner_protocol import PluginRunnerProtocolError
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor, _RunnerProcess, _StagedOperation
from a13n_service.plugins.runtime import PluginRuntimeLock, default_runtime_target
from anyio import CancelScope, sleep

pytestmark = pytest.mark.anyio


def _lock() -> PluginRuntimeLock:
    lock = PluginRuntimeLock(
        mode="runner",
        runtime_target=default_runtime_target(),
        worker_release="test",
        harness_version="test",
        digest="0" * 64,
    )
    return lock.model_copy(update={"digest": lock.computed_digest()})


def _runner(digest: str = "lock") -> _RunnerProcess:
    process = Mock(returncode=None)
    writer = Mock()
    writer.is_closing.return_value = False
    writer.wait_closed = AsyncMock()
    return _RunnerProcess("generation", digest, process, asyncio.StreamReader(), writer, Mock())


async def test_discovery_does_not_activate_a_staged_candidate(monkeypatch):
    lock = _lock()
    runner = _runner(lock.digest)
    request = AsyncMock()
    monkeypatch.setattr(_RunnerProcess, "request", request)
    supervisor = PluginRunnerSupervisor(Mock())
    supervisor._runners[lock.digest] = runner
    supervisor._operations["operation"] = _StagedOperation(lock.digest, "token")

    await supervisor.ensure_execution_runtime(lock)

    request.assert_not_awaited()
    assert runner.active_runtime_version is None


async def test_replayed_staging_reestablishes_child_readiness(monkeypatch):
    lock = _lock()
    supervisor = PluginRunnerSupervisor(Mock())
    supervisor._operations["operation"] = _StagedOperation(lock.digest, "token")
    ensure = AsyncMock(return_value=_runner(lock.digest))
    monkeypatch.setattr(supervisor, "_ensure_runner", ensure)

    assert await supervisor.stage_candidate(operation_id="operation", runtime_lock=lock) == "token"
    ensure.assert_awaited_once_with(lock)


@pytest.mark.parametrize("token", ["wrong-token", "token"])
async def test_abort_preserves_other_staging_reservations(monkeypatch, token):
    lock = _lock()
    runner = _runner(lock.digest)
    supervisor = PluginRunnerSupervisor(Mock())
    supervisor._runners[lock.digest] = runner
    supervisor._operations["first"] = _StagedOperation(lock.digest, "token")
    supervisor._operations["second"] = _StagedOperation(lock.digest, "other-token")
    stop = AsyncMock()
    monkeypatch.setattr(supervisor, "_stop_runner", stop)

    await supervisor.abort_candidate(operation_id="first", runtime_lock=lock, staging_token=token)

    assert ("first" in supervisor._operations) is (token != "token")
    assert "second" in supervisor._operations
    assert supervisor._runners[lock.digest] is runner
    stop.assert_not_awaited()


@pytest.mark.parametrize("failure", ["timeout", "generation", "protocol", "cancel"])
async def test_failed_request_invalidates_channel(monkeypatch, failure):
    runner = _runner()
    monkeypatch.setattr(module, "write_runner_message", AsyncMock())

    async def read(reader):
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "protocol":
            raise PluginRunnerProtocolError("invalid response")
        return {"type": "ACTIVE", "generation": "another-generation"}

    monkeypatch.setattr(module, "read_runner_message", read)
    error = asyncio.CancelledError if failure == "cancel" else PluginRunnerProtocolError
    with pytest.raises(error):
        await runner.request("ACTIVATE", "ACTIVE", timeout_seconds=0.01)
    runner.writer.close.assert_called_once()


async def test_request_deadline_includes_control_write(monkeypatch):
    runner = _runner()

    async def blocked_write(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "write_runner_message", blocked_write)
    with pytest.raises(PluginRunnerProtocolError, match="timed out"):
        async with asyncio.timeout(1):
            await runner.request("ACTIVATE", "ACTIVE", timeout_seconds=0.01)
    runner.writer.close.assert_called_once()


async def test_disconnected_child_is_replaced_even_before_process_exit(monkeypatch):
    lock = _lock()
    old = _runner(lock.digest)
    old.reader.feed_eof()
    replacement = _runner(lock.digest)
    supervisor = PluginRunnerSupervisor(Mock())
    supervisor._runners[lock.digest] = old
    start, stop = AsyncMock(return_value=replacement), AsyncMock()
    monkeypatch.setattr(supervisor, "_start_runner", start)
    monkeypatch.setattr(supervisor, "_stop_runner", stop)

    assert await supervisor._ensure_runner(lock) is replacement
    stop.assert_awaited_once_with(old)
    start.assert_awaited_once_with(lock)


async def test_cancelled_startup_reaps_the_spawned_process(monkeypatch):
    lock = _lock()
    materializer = Mock()
    materializer.materialize = AsyncMock(return_value=Mock(root=Path("unused-runtime")))
    supervisor = PluginRunnerSupervisor(materializer)
    stop = AsyncMock()
    monkeypatch.setattr(supervisor, "_stop_process", stop)
    listener = Mock()
    listener.sockets = [Mock(getsockname=lambda: ("127.0.0.1", 1234))]
    listener.wait_closed = AsyncMock()
    monkeypatch.setattr(module.asyncio, "start_server", AsyncMock(return_value=listener))
    spawned = asyncio.Event()
    process = Mock(returncode=None)

    async def wait():
        spawned.set()
        await asyncio.Event().wait()

    process.wait = wait
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(supervisor.stage_candidate(operation_id="operation", runtime_lock=lock))
    try:
        async with asyncio.timeout(1):
            await spawned.wait()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    stop.assert_awaited_once_with(process)
    assert supervisor.runtime_lock_digests == ()
    listener.close.assert_called_once()


@pytest.mark.parametrize("retired", [False, True])
async def test_retirement_only_removes_an_acknowledged_idle_historical_child(monkeypatch, retired):
    supervisor = PluginRunnerSupervisor(Mock())
    active, staged, historical = (_runner(name) for name in ("active", "staged", "historical"))
    supervisor._runners = {runner.runtime_lock_digest: runner for runner in (active, staged, historical)}
    supervisor._catalog_active_digest = "active"
    supervisor._operations["operation"] = _StagedOperation("staged", "token")
    active.active_runtime_version = historical.active_runtime_version = 1
    calls = []

    async def request(self, command, expected, **kwargs):
        calls.append((self.runtime_lock_digest, command, expected))
        return {"retired": retired}

    monkeypatch.setattr(_RunnerProcess, "request", request)
    stop = AsyncMock()
    monkeypatch.setattr(supervisor, "_stop_runner", stop)
    await supervisor._retire_idle()

    assert calls == [("historical", "RETIRE", "RETIRED")]
    assert ("historical" in supervisor._runners) is not retired
    assert "active" in supervisor._runners and "staged" in supervisor._runners
    if retired:
        stop.assert_awaited_once_with(historical)
    else:
        stop.assert_not_awaited()


@pytest.mark.parametrize("field", ["runtime_lock_digest", "runtime_version"])
async def test_discovery_rejects_mismatched_activation_acknowledgement(monkeypatch, field):
    lock = _lock()
    runner = _runner(lock.digest)
    supervisor = PluginRunnerSupervisor(Mock())
    supervisor._runners[lock.digest] = runner
    response = {"runtime_lock_digest": lock.digest, "runtime_version": 0, field: "mismatch"}
    monkeypatch.setattr(_RunnerProcess, "request", AsyncMock(return_value=response))
    with pytest.raises(PluginRuntimeCommandFailure, match="plugin_runtime_staging_invalid"):
        await supervisor.ensure_execution_runtime(lock)
    assert runner.active_runtime_version is None
    runner.writer.close.assert_called_once()


async def test_close_reaps_children_inside_a_cancelled_scope(monkeypatch):
    supervisor = PluginRunnerSupervisor(Mock())
    runner = _runner()
    supervisor._runners[runner.runtime_lock_digest] = runner
    stopped = []

    async def stop(child):
        await sleep(0)
        stopped.append(child)

    monkeypatch.setattr(supervisor, "_stop_runner", stop)
    with CancelScope() as scope:
        scope.cancel()
        await supervisor.close()
    assert stopped == [runner]
    assert supervisor.runtime_lock_digests == ()


@pytest.mark.parametrize("timeouts", [0, 1, 2])
async def test_process_stop_waits_then_escalates_only_after_each_deadline(timeouts):
    supervisor = PluginRunnerSupervisor(Mock())
    process = Mock(returncode=None)
    # Deterministic deadline expiry; real-process tests retain the production wait budget.
    process.wait = AsyncMock(side_effect=[*[TimeoutError for _ in range(timeouts)], 0])

    await supervisor._stop_process(process)

    assert process.wait.await_count == timeouts + 1
    assert process.terminate.call_count == (1 if timeouts >= 1 else 0)
    assert process.kill.call_count == (1 if timeouts == 2 else 0)


async def test_process_stop_does_not_signal_an_already_exited_child():
    supervisor = PluginRunnerSupervisor(Mock())
    process = Mock(returncode=0)
    process.wait = AsyncMock()

    await supervisor._stop_process(process)

    process.wait.assert_not_awaited()
    process.terminate.assert_not_called()
    process.kill.assert_not_called()

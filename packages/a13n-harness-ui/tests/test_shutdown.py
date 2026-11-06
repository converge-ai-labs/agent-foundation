"""Shutdown budget, admission ownership and dependency-order regressions."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from a13n_harness.environment.process_recovery import EnvdProcessState, ShellExecutionState
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.toolsets.process_manager import _await_cleanup_shielded, _OwnedProcess
from a13n_harness_ui.app import AppState, open_harness_ui_app
from a13n_harness_ui.errors import AppStateError
from a13n_harness_ui.shutdown import Shutdown
from a13n_harness_ui.tool_executions import ToolExecutions
from anyio import CancelScope, Event, create_task_group, fail_after, sleep_forever

from .test_app import _settings
from .test_mcp_inputs_app import input_configuration, install_input_model, pending

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def test_grace_is_shared_and_expedite_preserves_cleanup(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("a13n_harness_ui.shutdown.monotonic", lambda: clock[0])
    shutdown = Shutdown()
    shutdown.begin(10)
    clock[0] += 7
    shutdown.begin(10)
    assert shutdown.remaining == 3
    with shutdown.grace() as scope:
        shutdown.expedite()
        await sleep_forever()
    assert scope.cancel_called
    assert shutdown.remaining == 0
    with shutdown.grace() as next_scope:
        await sleep_forever()
    assert next_scope.cancel_called
    # Grace cancellation has ended; finalization remains possible.
    await asyncio.sleep(0)
    assert not shutdown._scopes


@pytest.mark.parametrize("shield_capture", [False, True])
async def test_admission_fence_uses_grace_and_keeps_storage_until_capture_exits(tmp_path, shield_capture):
    ready, stop, held, cancelled, release, closed = (Event() for _ in range(6))
    owners, errors = [], []

    async def lifetime():
        async with open_harness_ui_app(_settings(tmp_path / "state", shutdown_timeout_seconds=0.02)) as app:
            owners.append(app)
            ready.set()
            await stop.wait()
        closed.set()

    async def capture():
        app = owners[0]
        try:
            async with app._operation(), app._root_runs.require_inactive("thread-fence"):
                held.set()
                try:
                    await sleep_forever()
                finally:
                    cancelled.set()
                    if shield_capture:
                        with CancelScope(shield=True):
                            await release.wait()
                            await app._store.object_count()
        except AppStateError as exc:
            errors.append(exc.code)

    with fail_after(5):
        async with create_task_group() as group:
            group.start_soon(lifetime)
            await ready.wait()
            group.start_soon(capture)
            await held.wait()
            stop.set()
            await cancelled.wait()
            if shield_capture:
                assert not closed.is_set()
                assert owners[0].state is AppState.stopping
                release.set()
            await closed.wait()
    assert errors == ["app_stopping"]
    assert owners[0].shutdown.remaining == 0


@pytest.mark.parametrize("retained", [False, True])
async def test_mcp_input_is_settled_before_restart_boundary_drain(tmp_path, monkeypatch, retained):
    path = input_configuration(tmp_path, retained=retained)
    install_input_model(monkeypatch)
    async with open_harness_ui_app(
        _settings(tmp_path / "state", shutdown_timeout_seconds=2), configuration_path=path, host_mode="webui"
    ) as app:
        thread = await app.create_thread()
        await app.submit_thread(thread_id=thread.thread_id, prompt="Confirm")
        await pending(app, thread.thread_id)
    assert app._restart.committing
    assert not app._restart.errors
    assert thread.thread_id in app._restart.finalized


@pytest.mark.parametrize("explicit_cancel", [False, True])
async def test_retained_shell_reader_is_cancelled_before_supervisor_join(explicit_cancel):
    entered, started = asyncio.Event(), asyncio.Event()
    detached = []

    @asynccontextmanager
    async def dispatch(*args, **kwargs):
        yield None

    async def control(process_id, control, *, processes):
        entered.set()
        await asyncio.Event().wait()

    entry = SimpleNamespace(handle=object())
    process = object.__new__(_OwnedProcess)
    process.controller = SimpleNamespace(
        _environment=SimpleNamespace(
            retained_process_dispatch=dispatch,
            processes=SimpleNamespace(detach=lambda handle: detached.append(True)),
        ),
        _entry=lambda process_id: entry,
        _entries={"native": entry},
        control=control,
    )
    process.process_id = "native"
    process._condition = asyncio.Condition()
    process._readers = 0
    process._closed = False
    owner = ToolExecutions()

    async def shell():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await _await_cleanup_shielded(process.close(detach=True))

    shell_record = owner.runtime.admit(
        shell, thread_id="thread-a", run_id="run-a", tool_call_id="shell", tool_name="shell_exec"
    )
    await started.wait()
    owner.runtime._live[shell_record.execution_id].shell = ShellExecutionState(
        mount_name="native",
        process=EnvdProcessState(
            device_id="device-a",
            session_id="session-a",
            generation=1,
            start_operation_id="start-a",
            output_policy=EnvironmentOutputPolicy(max_inline_bytes=1000, max_output_bytes=1000, overflow="truncate"),
        ),
    )

    async def reader():
        return await process.call({"operation": "wait", "timeout_seconds": 180})

    reader_record = owner.runtime.admit(
        reader, thread_id="thread-a", run_id="run-a", tool_call_id="reader", tool_name="run_code"
    )
    await entered.wait()
    if explicit_cancel:
        owner.runtime.request_cancellation("thread-a", shell_record.execution_id)
    try:
        with fail_after(2):
            await owner.close(timeout_seconds=0)
        assert process._readers == 0
        assert detached == [True]
        record = await owner.runtime.info("thread-a", reader_record.execution_id)
        assert record.status == "cancelled"
        shell_result = await owner.runtime.result("thread-a", shell_record.execution_id)
        assert shell_result.record.cancel_requested is explicit_cancel
        assert shell_result.record.terminal is explicit_cancel
    finally:
        # Also terminate the fixture on the old, deadlocking implementation.
        owner.runtime.request_cancellation("thread-a", reader_record.execution_id)
        await owner.runtime.join()

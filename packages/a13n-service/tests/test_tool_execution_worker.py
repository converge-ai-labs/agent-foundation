"""Worker shutdown and lease supervision use the Harness executor, including bounded process exit."""

import asyncio
import sys
import textwrap
from dataclasses import replace

import pytest
from a13n_service.infra import outbox
from a13n_service.runs import worker as worker_module
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from a13n_service.runs.tool_executions import authority
from a13n_service.runs.tool_executions.delivery import tool_results
from a13n_service.runs.tool_executions.store import ORIGIN
from a13n_service.runs.worker import Worker

pytestmark = pytest.mark.anyio


async def test_idle_tool_heartbeat_needs_no_database(runtime, monkeypatch):
    def unavailable(*args):
        pytest.fail("An idle tool heartbeat must not acquire a database connection")

    monkeypatch.setattr(authority, "transaction", unavailable)
    assert not runtime.executions.active
    assert await authority.heartbeat(runtime, "worker-idle") == ((), set())


async def _setup(service, scripted_model, runs_kit, *, grace=0.01, lease_seconds=30):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    started = await runs_kit.start_thread(service, agent, "work")
    (lease,) = await claim(service.runtime, worker_id="worker-a", worker_build="test", limit=1)
    settings = service.runtime.settings
    runtime = replace(
        service.runtime,
        settings=settings.model_copy(
            update={
                "worker": settings.worker.model_copy(
                    update={"authority_seconds": 0.01, "lease_seconds": lease_seconds}
                ),
                "tool_executions": settings.tool_executions.model_copy(
                    update={
                        "shutdown_grace_seconds": grace,
                        "finalization_seconds": 2,
                    }
                ),
            }
        ),
    )
    return runtime, lease, started


def _admit(runtime, lease, operation, *, release=None):
    token = ORIGIN.set(lease)
    try:
        record = runtime.executions.admit(
            operation,
            thread_id=lease.thread_id,
            run_id="harness-original",
            tool_call_id="call-original",
            tool_name="slow_tool",
            release=release,
        )
        runtime.executions.release_foreground(lease.thread_id, record.execution_id)
        return record
    finally:
        ORIGIN.reset(token)


@pytest.mark.parametrize("natural", [True, False])
async def test_shutdown_keeps_heartbeat_and_publishes_before_closing(service, scripted_model, runs_kit, natural):
    runtime, lease, _ = await _setup(service, scripted_model, runs_kit, grace=2 if natural else 0.01)
    entered, finish = asyncio.Event(), asyncio.Event()
    cleanup_started, cleanup = asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        await finish.wait()
        return "finished naturally"

    async def release():
        cleanup_started.set()
        await cleanup.wait()

    record = _admit(runtime, lease, operation, release=release)
    await asyncio.wait_for(entered.wait(), 5)
    scripted_model.say("The original Run finishes first")
    await execute(runtime, lease, AttemptControl())
    worker = Worker(runtime, execute)
    worker.id = lease.worker_id
    supervisor = asyncio.create_task(worker._supervise())
    draining = asyncio.create_task(worker._drain())
    try:
        if natural:
            # Observe admission closure, then allow an original invocation to settle inside its grace window.
            async with asyncio.timeout(5):
                while not worker.stopping:
                    await asyncio.sleep(0)
            finish.set()
        await asyncio.wait_for(cleanup_started.wait(), 5)
        assert runtime.executions.active and not draining.done()
        cleanup.set()
        await asyncio.wait_for(draining, 5)
        result = await runtime.executions.result(lease.thread_id, record.execution_id)
        assert result.record.status == ("succeeded" if natural else "cancelled")
        if natural:
            assert result.result == "finished naturally"
        else:
            assert "Worker shutdown" in result.record.error
        # Publication survives the worker. A control process delivers and admits a successor normally.
        claims = await outbox.claim(
            runtime.storage, "tool_result", owner="control", limit=10, lease_seconds=60, max_attempts=12
        )
        assert len(claims) == 1
        await tool_results(service.runtime)(claims[0])
        (successor,) = await claim(service.runtime, worker_id="worker-b", worker_build="test", limit=1)
        assert successor.thread_id == lease.thread_id
    finally:
        finish.set()
        cleanup.set()
        supervisor.cancel()
        await asyncio.gather(supervisor, draining, return_exceptions=True)
        await runtime.executions.close()


async def test_failed_heartbeat_cancels_locally_once_without_waiting_for_database(
    service,
    scripted_model,
    runs_kit,
    monkeypatch,
):
    runtime, lease, _ = await _setup(service, scripted_model, runs_kit, lease_seconds=0.6)
    entered, cancelled, cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = 0

    async def operation():
        nonlocal cancellations
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations += 1
            cancelled.set()
            await cleanup.wait()
            raise

    async def hanging_heartbeat(*args):
        await asyncio.Event().wait()

    async def forbidden_cancel(*args):
        pytest.fail("Local lease cancellation must not call the database-backed cancel operation")

    record = _admit(runtime, lease, operation)
    await asyncio.wait_for(entered.wait(), 5)
    monkeypatch.setattr(worker_module, "heartbeat", hanging_heartbeat)
    monkeypatch.setattr(runtime.executions, "cancel", forbidden_cancel)
    supervisor = asyncio.create_task(Worker(runtime, execute)._supervise())
    try:
        await asyncio.wait_for(cancelled.wait(), 2)
        # Repeated failed heartbeats must leave cancellation cleanup intact.
        await asyncio.sleep(0.08)
        assert cancellations == 1 and runtime.executions.active
        cleanup.set()
        await runtime.executions.join()
        result = await runtime.executions.result(lease.thread_id, record.execution_id)
        # The artificial lease may have expired before publication; either way the invocation is stopped.
        assert result is None or result.record.status in {"cancelled", "lost"}
        assert not runtime.executions.active
    finally:
        cleanup.set()
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)
        await runtime.executions.close()


@pytest.mark.parametrize("cause", ["shutdown", "lease_loss", "missing_owner", "other_renewed"])
async def test_uncooperative_tool_cannot_extend_worker_process_shutdown(cause):
    # Exercise the real os._exit boundary outside the test process. asyncio.run would otherwise wait forever.
    script = textwrap.dedent("""
        import asyncio
        import sys
        from types import SimpleNamespace
        from a13n_harness.tool_execution import ToolExecutionRuntime
        from a13n_service.runs.worker import Worker
        from a13n_service.runs import worker as worker_module

        async def main():
            entered = asyncio.Event()
            async def operation():
                entered.set()
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        pass
            executions = ToolExecutionRuntime()
            record = executions.admit(operation, thread_id="thread", run_id="run", tool_call_id="call", tool_name="stuck")
            executions.lease_deadlines = {record.execution_id: asyncio.get_running_loop().time() + 0.15}
            await entered.wait()
            runtime = SimpleNamespace(executions=executions, settings=SimpleNamespace(
                worker=SimpleNamespace(slots=1, drain_seconds=0, authority_seconds=0.01, lease_seconds=0.15),
                tool_executions=SimpleNamespace(shutdown_grace_seconds=0.01, finalization_seconds=0.01),
            ))
            worker = Worker(runtime, None)
            if sys.argv[1] != "shutdown":
                async def heartbeat(*args):
                    if sys.argv[1] == "lease_loss":
                        raise ConnectionError("unavailable")
                    return (), {"other-execution"} if sys.argv[1] == "other_renewed" else set()
                worker_module.heartbeat = heartbeat
                await worker._supervise()
            else:
                await worker._drain()
            raise AssertionError("shutdown returned with uncooperative work")
        asyncio.run(main())
    """)
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, cause, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(10):
            _, stderr = await process.communicate()
        assert process.returncode == 1
        assert b"forcing process exit" in stderr
        assert b"Traceback" not in stderr
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_closed_tool_admission_exits_worker_with_all_slots_busy():
    script = textwrap.dedent("""
        import asyncio
        from types import SimpleNamespace
        from a13n_harness.tool_execution import ToolExecutionRuntime
        from a13n_service.runs.worker import Worker
        from a13n_service.runs import worker as worker_module

        async def main():
            async def idle(*args):
                await asyncio.Event().wait()
            async def heartbeat(*args):
                return (), set()
            worker_module.heartbeat = heartbeat
            executions = ToolExecutionRuntime()
            executions.routing = SimpleNamespace(run=idle)
            executions.output = SimpleNamespace(run=idle)
            executions.lease_deadlines = {}
            runtime = SimpleNamespace(executions=executions, settings=SimpleNamespace(
                worker=SimpleNamespace(slots=1, drain_seconds=0, authority_seconds=0.01, lease_seconds=30),
                tool_executions=SimpleNamespace(shutdown_grace_seconds=0.01, finalization_seconds=0.01),
            ))
            worker = Worker(runtime, None)
            worker.free = 0
            asyncio.get_running_loop().call_later(0.03, executions.stop_admission)
            await worker.run()
            raise AssertionError("closed worker stayed alive")
        asyncio.run(main())
    """)
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(10):
            _, stderr = await process.communicate()
        assert process.returncode == 1
        assert b"forcing process exit after drain" in stderr
        assert b"Traceback" not in stderr
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("cause", ["output", "routing", "supervisor", "drain", "shutdown"])
async def test_worker_fatal_boundary_preserves_normal_shutdown(cause):
    script = textwrap.dedent("""
        import asyncio
        import sys
        from types import SimpleNamespace
        from a13n_harness.tool_execution import ToolExecutionRuntime
        from a13n_service.runs.worker import Worker

        async def main():
            cause = sys.argv[1]
            entered = asyncio.Event()
            drained = asyncio.Event()
            async def idle(*args):
                entered.set()
                await asyncio.Event().wait()
            async def fail(*args):
                await entered.wait()
                raise RuntimeError("injected worker failure")
            executions = ToolExecutionRuntime()
            executions.routing = SimpleNamespace(run=fail if cause == "routing" else idle)
            executions.output = SimpleNamespace(run=fail if cause == "output" else idle)
            runtime = SimpleNamespace(executions=executions, settings=SimpleNamespace(
                worker=SimpleNamespace(slots=1, drain_seconds=0, authority_seconds=0.01, lease_seconds=30),
                tool_executions=SimpleNamespace(shutdown_grace_seconds=0.01, finalization_seconds=0.01),
            ))
            worker = Worker(runtime, None)
            worker.free = 0
            worker._supervise = fail if cause == "supervisor" else idle
            original = worker._drain
            async def drain():
                if cause == "drain":
                    raise RuntimeError("injected drain failure")
                await original()
                drained.set()
            worker._drain = drain
            task = asyncio.create_task(worker.run())
            await entered.wait()
            if cause in {"drain", "shutdown"}:
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                assert cause == "shutdown"
                assert drained.is_set() and not executions.accepting
                return
            raise AssertionError("failed worker stayed alive")
        asyncio.run(main())
    """)
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, cause, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(10):
            _, stderr = await process.communicate()
        assert process.returncode == (0 if cause == "shutdown" else 1), stderr.decode()
        assert (b"Worker failed; forcing process exit" in stderr) == (cause != "shutdown")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

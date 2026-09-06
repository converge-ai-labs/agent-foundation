"""Real Runner processes, production resources, and native model HTTP execution."""

import asyncio
import json
import os
from contextlib import asynccontextmanager

import pytest
from a13n_service.app import create_app
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.plugins.materialization import PluginRuntimeMaterializer
from a13n_service.plugins.models import PluginRuntimeLockRecord
from a13n_service.plugins.objects import PluginObjectStore
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.plugins.runtime import (
    PluginRuntimeLock,
    WorkerReleaseManifest,
    default_runtime_target,
    installed_distribution_versions,
    installed_harness_version,
)
from a13n_service.settings import ProcessRole
from a13n_service.storage import open_storage, short_session
from anyio import Event, fail_after
from sqlalchemy import select

from ..models.conftest import ORG_ID
from .test_execution import _configure, _start, _stream_reply, _terminal

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def _model_server(*, hold_first=False):
    requests = []
    entered = Event()
    disconnected = Event()
    tasks = set()

    async def handle(reader, writer):
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            headers = {}
            for line in raw.decode().split("\r\n")[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.lower()] = value.strip()
            body = await reader.readexactly(int(headers["content-length"]))
            assert headers["authorization"] == "Bearer test-only"
            requests.append(json.loads(body))
            entered.set()
            if hold_first and len(requests) == 1:
                try:
                    await reader.read()
                except ConnectionResetError:
                    pass
                finally:
                    disconnected.set()
                return
            response = _stream_reply()
            body = response.content
            writer.write(
                f"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionResetError:
                pass

    def connected(reader, writer):
        task = asyncio.create_task(handle(reader, writer))
        tasks.add(task)

    server = await asyncio.start_server(connected, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1", requests, entered, disconnected
    finally:
        server.close()
        for task in tasks:
            if not task.done():
                task.cancel()
        async with asyncio.timeout(5):
            await asyncio.gather(*tasks, return_exceptions=True)
            await server.wait_closed()


async def test_runner_storage_passes_unmodified_production_probe(runner_settings):
    async with open_storage(runner_settings.storage_settings()):
        pass


async def test_discovery_starts_real_runner_and_seals_native_execution(runner_settings):
    async with _model_server() as (url, requests, _entered, _closed):
        app = create_app(runner_settings)
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            assert runtime.worker.execution_loop is None
            assert runtime.worker.runner_discovery.ready
            accepted = await _start(runtime, await _configure(runtime, base_url=url))
            run = await _terminal(runtime, accepted.run_id)
            assert run.status.value == "completed", run.failure
            assert run.output == "Worker finished"
            assert len(requests) == 1
            state = await RunStateStore(runtime.shared.storage.objects).read(ORG_ID, run.id)
            assert state.digest_sha256 == run.sealed_state.digest_sha256
            supervisor = runtime.worker.plugin_runtime
            child = supervisor._runners[run.runtime_lock_digest]
            assert child.process.pid != os.getpid()
            async with short_session(runtime.shared.storage.sessions) as database:
                attempt = (
                    await database.scalars(select(RunAttemptRecord).where(RunAttemptRecord.run_id == run.id))
                ).one()
                assert attempt.worker_id == supervisor.worker_id
                assert attempt.worker_generation == child.generation
                assert attempt.worker_build_id == supervisor.build_id
                assert attempt.runtime_lock_digest == run.runtime_lock_digest
                assert attempt.harness_run_id is not None
        assert child.process.returncode == 0


@pytest.mark.parametrize("stop", ["kill", "sigterm", "ipc_disconnect"])
async def test_runner_crash_reconstructs_same_lock_and_waits_for_lease_expiry(runner_settings, stop):
    async with _model_server(hold_first=True) as (url, requests, entered, closed):
        app = create_app(runner_settings)
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            accepted = await _start(runtime, await _configure(runtime, base_url=url))
            with fail_after(15):
                await entered.wait()
            async with short_session(runtime.shared.storage.sessions) as database:
                run = (await database.get(RunRecord, accepted.run_id)).to_resource()
            supervisor = runtime.worker.plugin_runtime
            old = supervisor._runners[run.runtime_lock_digest]
            # Pause only local reconstruction while observing the dead generation.
            # PostgreSQL renewal and expiry still run independently in the child.
            async with supervisor._lock:
                if stop == "kill":
                    old.process.kill()
                elif stop == "sigterm":
                    old.process.terminate()
                else:
                    old.writer.close()
                    await old.writer.wait_closed()
                with fail_after(10):
                    await old.process.wait()
                if stop == "sigterm":
                    assert old.process.returncode == 0
                assert not supervisor.ready
                async with short_session(runtime.shared.storage.sessions) as database:
                    current = (await database.get(RunRecord, run.id)).to_resource()
                    assert current.attempts_started == 1
                    assert current.sealed_at is None
            with fail_after(15):
                await closed.wait()
            completed = await _terminal(runtime, run.id)
            assert completed.status.value == "completed", completed.failure
            assert completed.attempts_started == 2
            assert len(requests) == 2
            new = supervisor._runners[run.runtime_lock_digest]
            assert new.generation != old.generation
            async with short_session(runtime.shared.storage.sessions) as database:
                attempts = (
                    await database.scalars(
                        select(RunAttemptRecord)
                        .where(RunAttemptRecord.run_id == run.id)
                        .order_by(RunAttemptRecord.attempt_number)
                    )
                ).all()
                assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]
                assert attempts[1].claimed_at >= attempts[0].lease_expires_at


async def test_runner_drain_joins_execution_without_releasing_durable_lease(runner_settings):
    async with _model_server(hold_first=True) as (url, _requests, entered, closed):
        app = create_app(runner_settings)
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            accepted = await _start(runtime, await _configure(runtime, base_url=url))
            with fail_after(15):
                await entered.wait()
            await runtime.worker.runner_discovery.drain()
            assert not runtime.worker.runner_discovery.ready
            with fail_after(5):
                await closed.wait()
            async with short_session(runtime.shared.storage.sessions) as database:
                run = (await database.get(RunRecord, accepted.run_id)).to_resource()
                assert run.sealed_at is None
                attempt = await database.get(RunAttemptRecord, run.current_run_attempt_id)
                assert attempt.status == "running"
            child = runtime.worker.plugin_runtime._runners[run.runtime_lock_digest]
            status = await child.request("STATUS", "STATUS", timeout_seconds=2)
            assert status["active_count"] == 0
            assert status["ready"] is False


async def test_staged_runner_cannot_claim_until_activation(runner_settings):
    async with _model_server() as (url, requests, _entered, _closed):
        app = create_app(runner_settings.model_copy(update={"role": ProcessRole.control}))
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            accepted = await _start(runtime, await _configure(runtime, base_url=url))
            storage = runtime.shared.storage
            async with short_session(storage.sessions) as database:
                run = (await database.get(RunRecord, accepted.run_id)).to_resource()
                record = await database.get(PluginRuntimeLockRecord, run.runtime_lock_digest)
                lock = PluginRuntimeLock.model_validate(record.manifest)
            materializer = await PluginRuntimeMaterializer.create(
                storage.files_root,
                PluginObjectStore(storage.objects),
                WorkerReleaseManifest(
                    worker_release=runner_settings.build_version,
                    harness_version=installed_harness_version(),
                    runtime_target=default_runtime_target(),
                    distributions=installed_distribution_versions(),
                ),
                max_wheel_bytes=runner_settings.plugin_max_wheel_bytes,
                max_expanded_bytes=runner_settings.plugin_max_expanded_bytes,
                max_archive_members=runner_settings.plugin_max_archive_members,
                max_runtime_bytes=runner_settings.plugin_runtime_max_materialized_bytes,
            )
            async with PluginRunnerSupervisor(materializer, settings=runner_settings) as supervisor:
                token = await supervisor.stage_candidate(operation_id="stage-test", runtime_lock=lock)
                await supervisor.ensure_execution(lock)
                await asyncio.sleep(0.2)
                async with short_session(storage.sessions) as database:
                    pending = await database.get(RunRecord, run.id)
                    assert pending.attempts_started == 0
                assert requests == []
                await supervisor.activate_candidate(
                    operation_id="stage-test",
                    runtime_lock=lock,
                    staging_token=token,
                    runtime_generation=2,
                )
                completed = await _terminal(runtime, run.id)
                assert completed.status.value == "completed", completed.failure
                assert len(requests) == 1

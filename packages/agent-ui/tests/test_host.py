from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import a13n_ui.host as host_module
import a13n_ui.storage.runtime as storage_runtime
import pytest
from a13n_ui.errors import HostStateError, ObjectIntegrityError
from a13n_ui.host import AgentUiHost, HostState, open_agent_ui_host
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.storage import ObjectKind
from anyio import TASK_STATUS_IGNORED, create_task_group, fail_after, sleep, sleep_forever
from anyio import Event as AsyncEvent
from anyio.abc import TaskStatus

pytestmark = pytest.mark.anyio


def _settings(root: Path) -> AgentUiSettings:
    return AgentUiSettings(storage=StorageSettings(data_root=root))


async def test_host_runtime_restart_keeps_the_host_and_store_generation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_agent_ui_host(settings) as host:
        process_generation = (await host.status()).process_generation
        first_runtime = (await host.runtime_status()).active_generation_id
        result = await host.restart_runtime()
        assert result.previous_generation_id == first_runtime
        assert result.active.generation_id != first_runtime
        assert (await host.status()).process_generation == process_generation


async def test_application_starts_publishes_restarts_and_closes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_agent_ui_host(settings) as application:
        retained_application = application
        first_generation = (await application.status()).process_generation
        assert application.state is HostState.ready
        assert (await application.status()).object_count == 0
        reference = await application._store.publish_object(
            object_kind=ObjectKind.agent_snapshot,
            object_schema_version="1",
            payload={"agent": "root"},
        )
        assert (await application._store.read_object(reference)).payload == {"agent": "root"}
        assert (await application.status()).object_count == 1

    assert retained_application.state is HostState.closed
    with pytest.raises(HostStateError) as closed:
        await retained_application.status()
    assert closed.value.code == "host_not_ready"

    async with open_agent_ui_host(settings) as restarted:
        assert (await restarted._store.read_object(reference)).payload == {"agent": "root"}
        assert (await restarted.status()).process_generation != first_generation


async def test_application_shutdown_drains_an_accepted_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    shutdown = AsyncEvent()
    close_completed = AsyncEvent()
    operation_started = AsyncEvent()
    release_operation = AsyncEvent()
    statuses: list[object] = []

    async with create_task_group() as tasks:
        application = await tasks.start(_run_until_shutdown, settings, shutdown, close_completed)

        async def slow_object_count() -> int:
            operation_started.set()
            await release_operation.wait()
            return 0

        async def read_status() -> None:
            statuses.append(await application.status())

        monkeypatch.setattr(application._store, "object_count", slow_object_count)
        tasks.start_soon(read_status)
        await operation_started.wait()
        shutdown.set()
        with fail_after(2):
            while application.state is not HostState.stopping:
                await sleep(0)
        assert not close_completed.is_set()
        release_operation.set()
        await close_completed.wait()

    assert len(statuses) == 1
    assert application.state is HostState.closed


async def test_application_shutdown_cancels_an_operation_after_its_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AgentUiSettings(storage=StorageSettings(data_root=tmp_path), shutdown_timeout_seconds=0.01)
    shutdown = AsyncEvent()
    close_completed = AsyncEvent()
    operation_started = AsyncEvent()
    errors: list[HostStateError] = []

    async with create_task_group() as tasks:
        application = await tasks.start(_run_until_shutdown, settings, shutdown, close_completed)

        async def stalled_object_count() -> int:
            operation_started.set()
            await sleep_forever()

        async def read_status() -> None:
            try:
                await application.status()
            except HostStateError as exc:
                errors.append(exc)

        monkeypatch.setattr(application._store, "object_count", stalled_object_count)
        tasks.start_soon(read_status)
        await operation_started.wait()
        shutdown.set()
        await close_completed.wait()

    assert application.state is HostState.closed
    assert len(errors) == 1
    assert errors[0].code == "host_stopping"


async def test_application_closes_after_external_cancellation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with create_task_group() as tasks:
        application = await tasks.start(_hold_application, settings)
        tasks.cancel_scope.cancel()

    assert application.state is HostState.closed


async def test_application_marks_closed_when_store_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    original_open_store = host_module.open_local_store

    @asynccontextmanager
    async def failing_close(storage: StorageSettings) -> AsyncGenerator[storage_runtime.LocalStore]:
        async with original_open_store(storage) as store:
            yield store
        raise RuntimeError("store close failed")

    monkeypatch.setattr(host_module, "open_local_store", failing_close)
    retained: AgentUiHost | None = None
    with pytest.raises(RuntimeError, match="store close failed"):
        async with open_agent_ui_host(settings) as application:
            retained = application

    assert retained is not None
    assert retained.state is HostState.closed


async def test_concurrent_hosts_share_one_data_root(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_agent_ui_host(settings) as first:
        async with open_agent_ui_host(settings) as second:
            assert first.state is HostState.ready
            assert second.state is HostState.ready
            assert (await first.status()).process_generation != (await second.status()).process_generation


async def test_missing_unselected_object_does_not_block_startup_but_fails_on_read(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_agent_ui_host(settings) as application:
        reference = await application._store.publish_object(
            object_kind=ObjectKind.environment_snapshot,
            object_schema_version="1",
            payload={"environment": "local"},
        )

    next((tmp_path / "objects").rglob("*.json.zst")).unlink()

    async with open_agent_ui_host(settings) as restarted:
        with pytest.raises(ObjectIntegrityError) as missing:
            await restarted._store.read_object(reference)
    assert missing.value.code == "object_unreadable"


async def _run_until_shutdown(
    settings: AgentUiSettings,
    shutdown: AsyncEvent,
    close_completed: AsyncEvent,
    *,
    task_status: TaskStatus[AgentUiHost] = TASK_STATUS_IGNORED,
) -> None:
    async with open_agent_ui_host(settings) as application:
        task_status.started(application)
        await shutdown.wait()
    close_completed.set()


async def _hold_application(
    settings: AgentUiSettings,
    *,
    task_status: TaskStatus[AgentUiHost] = TASK_STATUS_IGNORED,
) -> None:
    async with open_agent_ui_host(settings) as application:
        task_status.started(application)
        await sleep_forever()

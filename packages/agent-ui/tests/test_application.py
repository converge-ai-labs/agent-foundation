from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import NoReturn

import converge_agent_ui.application as application_module
import converge_agent_ui.storage.runtime as storage_runtime
import pytest
from anyio import TASK_STATUS_IGNORED, CancelScope, create_task_group, fail_after, sleep, sleep_forever
from anyio import Event as AsyncEvent
from anyio.abc import TaskStatus
from converge_agent_ui.application import AgentUiApplication, ApplicationState, open_application
from converge_agent_ui.errors import (
    ApplicationStateError,
    ObjectIntegrityError,
    StoreIntegrityError,
    StoreLeaseConflict,
)
from converge_agent_ui.settings import AgentUiSettings, StorageSettings
from converge_agent_ui.storage import ObjectEnvelope, ObjectKind, short_session
from converge_agent_ui.storage.models import StoreLeaseRecord
from pydantic import JsonValue

pytestmark = pytest.mark.anyio


def _settings(root: Path, *, heartbeat: float = 2.0) -> AgentUiSettings:
    return AgentUiSettings(
        storage=StorageSettings(
            data_root=root,
            lease_heartbeat_seconds=heartbeat,
        )
    )


async def test_application_starts_publishes_restarts_and_closes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_application(settings) as application:
        retained_application = application
        first_generation = (await application.status()).process_generation
        assert application.state is ApplicationState.ready
        assert (await application.status()).registered_object_count == 0
        reference = await application._store.publish_object(
            object_kind=ObjectKind.agent_snapshot,
            object_schema_version="1",
            payload={"agent": "root"},
        )
        assert (await application._store.read_object(reference)).payload == {"agent": "root"}
        assert (await application.status()).registered_object_count == 1

    assert retained_application.state is ApplicationState.closed
    with pytest.raises(ApplicationStateError) as closed:
        await retained_application.status()
    assert closed.value.code == "application_not_ready"

    async with open_application(settings) as restarted:
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
            while application.state is not ApplicationState.stopping:
                await sleep(0)
        assert not close_completed.is_set()
        async with short_session(application._store.database.sessions) as session:
            assert await session.get(StoreLeaseRecord, 1) is not None
        release_operation.set()
        await close_completed.wait()

    assert len(statuses) == 1
    assert application.state is ApplicationState.closed


async def test_application_shutdown_cancels_an_operation_after_its_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AgentUiSettings(
        storage=StorageSettings(data_root=tmp_path),
        shutdown_timeout_seconds=0.01,
    )
    shutdown = AsyncEvent()
    close_completed = AsyncEvent()
    operation_started = AsyncEvent()
    errors: list[ApplicationStateError] = []

    async with create_task_group() as tasks:
        application = await tasks.start(_run_until_shutdown, settings, shutdown, close_completed)

        async def stalled_object_count() -> int:
            operation_started.set()
            await sleep_forever()

        async def read_status() -> None:
            try:
                await application.status()
            except ApplicationStateError as exc:
                errors.append(exc)

        monkeypatch.setattr(application._store, "object_count", stalled_object_count)
        tasks.start_soon(read_status)
        await operation_started.wait()
        shutdown.set()
        await close_completed.wait()

    assert application.state is ApplicationState.closed
    assert len(errors) == 1
    assert errors[0].code == "application_stopping"


async def test_application_closes_after_external_cancellation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with create_task_group() as tasks:
        application = await tasks.start(_hold_application, settings)
        tasks.cancel_scope.cancel()

    assert application.state is ApplicationState.closed
    async with storage_runtime.open_database(tmp_path / "metadata.sqlite3", settings.storage) as database:
        async with short_session(database.sessions) as session:
            assert await session.get(StoreLeaseRecord, 1) is None


async def test_application_marks_closed_when_store_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    original_open_store = application_module.open_local_store

    @asynccontextmanager
    async def failing_close(storage: StorageSettings) -> AsyncGenerator[storage_runtime.LocalStore]:
        async with original_open_store(storage) as store:
            yield store
        raise RuntimeError("store close failed")

    monkeypatch.setattr(application_module, "open_local_store", failing_close)
    retained: AgentUiApplication | None = None
    with pytest.raises(RuntimeError, match="store close failed"):
        async with open_application(settings) as application:
            retained = application

    assert retained is not None
    assert retained.state is ApplicationState.closed


async def test_application_rejects_a_concurrent_data_root_owner(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_application(settings):
        with pytest.raises(StoreLeaseConflict) as conflict:
            async with open_application(settings):
                pytest.fail("a second application unexpectedly acquired the data root")

    assert conflict.value.code == "store_lease_conflict"


async def test_store_lease_heartbeats_and_releases_last(tmp_path: Path) -> None:
    settings = _settings(tmp_path, heartbeat=0.02)

    async with open_application(settings) as application:
        store = application._store
        async with short_session(store.database.sessions) as session:
            initial = await session.get(StoreLeaseRecord, 1)
            assert initial is not None
            acquired_at = initial.acquired_at
            initial_heartbeat = initial.heartbeat_at
        with fail_after(2):
            while True:
                async with short_session(store.database.sessions) as session:
                    updated = await session.get(StoreLeaseRecord, 1)
                    assert updated is not None
                    assert updated.process_generation == application._store.process_generation
                    if _comparable(updated.heartbeat_at) > _comparable(initial_heartbeat):
                        break
                await sleep(0.01)
        assert _comparable(updated.heartbeat_at) >= _comparable(acquired_at)

    async with storage_runtime.open_database(tmp_path / "metadata.sqlite3", settings.storage) as database:
        async with short_session(database.sessions) as session:
            assert await session.get(StoreLeaseRecord, 1) is None


async def test_startup_quarantines_malformed_staging_and_records_diagnostic(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir(parents=True)
    (staging / "broken.tmp").write_bytes(b"broken")

    async with open_application(settings) as application:
        diagnostics = await application.recovery_diagnostics()
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "staging_quarantined"
        assert diagnostics[0].detail == "broken.tmp"
        assert diagnostics[0].process_generation == (await application.status()).process_generation

    assert list(staging.iterdir()) == []
    assert len(list((tmp_path / "quarantine").iterdir())) == 1


async def test_missing_unselected_object_does_not_block_startup_but_fails_on_read(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_application(settings) as application:
        reference = await application._store.publish_object(
            object_kind=ObjectKind.environment_snapshot,
            object_schema_version="1",
            payload={"environment": "local"},
        )

    next((tmp_path / "objects").rglob("*.json.zst")).unlink()

    async with open_application(settings) as restarted:
        with pytest.raises(ObjectIntegrityError) as missing:
            await restarted._store.read_object(reference)
    assert missing.value.code == "object_unreadable"


async def test_invalid_unselected_registration_does_not_block_startup_but_fails_on_read(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_application(settings) as application:
        reference = await application._store.publish_object(
            object_kind=ObjectKind.agent_snapshot,
            object_schema_version="1",
            payload={"agent": "root"},
        )

    with sqlite3.connect(tmp_path / "metadata.sqlite3") as connection:
        connection.execute("UPDATE immutable_object SET object_kind = 'unknown-kind'")

    async with open_application(settings) as restarted:
        with pytest.raises(StoreIntegrityError) as invalid:
            await restarted._store.read_object(reference)
    assert invalid.value.code == "object_registration_mismatch"


async def test_malformed_registration_timestamp_fails_with_bounded_integrity_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async with open_application(settings) as application:
        reference = await application._store.publish_object(
            object_kind=ObjectKind.agent_snapshot,
            object_schema_version="1",
            payload={"agent": "root"},
        )

    with sqlite3.connect(tmp_path / "metadata.sqlite3") as connection:
        connection.execute("UPDATE immutable_object SET created_at = 'not-a-date'")

    async with open_application(settings) as restarted:
        with pytest.raises(StoreIntegrityError) as invalid:
            await restarted._store.read_object(reference)
    assert invalid.value.code == "object_registration_invalid"


async def test_cancelled_publication_never_registers_an_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)

    async with open_application(settings) as application:
        objects = application._store.objects
        original_publish = objects._publish
        started = Event()
        release = Event()

        def delayed_publish(
            *,
            object_kind: ObjectKind,
            object_schema_version: str,
            payload: JsonValue,
            payload_codec_version: str,
            created_at: datetime | None,
        ) -> ObjectEnvelope:
            started.set()
            if not release.wait(timeout=2):
                raise RuntimeError("publication test timed out")
            return original_publish(
                object_kind=object_kind,
                object_schema_version=object_schema_version,
                payload=payload,
                payload_codec_version=payload_codec_version,
                created_at=created_at,
            )

        async def publish(
            *,
            task_status: TaskStatus[CancelScope] = TASK_STATUS_IGNORED,
        ) -> None:
            with CancelScope() as scope:
                task_status.started(scope)
                await application._store.publish_object(
                    object_kind=ObjectKind.provider_state,
                    object_schema_version="1",
                    payload={"provider": "local"},
                )

        monkeypatch.setattr(objects, "_publish", delayed_publish)
        async with create_task_group() as tasks:
            cancel_scope = await tasks.start(publish)
            with fail_after(2):
                while not started.is_set():
                    await sleep(0)
            cancel_scope.cancel()
            release.set()

        assert await application._store.object_count() == 0
        assert len(list((tmp_path / "objects").rglob("*.json.zst"))) == 1


async def test_failed_registration_leaves_an_unselected_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)

    @asynccontextmanager
    async def fail_transaction(*_args: object, **_kwargs: object) -> AsyncGenerator[NoReturn]:
        raise RuntimeError("registration failed")
        yield  # pragma: no cover

    async with open_application(settings) as application:
        original_transaction = storage_runtime.transaction
        monkeypatch.setattr(storage_runtime, "transaction", fail_transaction)
        try:
            with pytest.raises(RuntimeError, match="registration failed"):
                await application._store.publish_object(
                    object_kind=ObjectKind.provider_state,
                    object_schema_version="1",
                    payload={"provider": "local"},
                )
            assert await application._store.object_count() == 0
            assert len(list((tmp_path / "objects").rglob("*.json.zst"))) == 1
        finally:
            monkeypatch.setattr(storage_runtime, "transaction", original_transaction)


async def _run_until_shutdown(
    settings: AgentUiSettings,
    shutdown: AsyncEvent,
    close_completed: AsyncEvent,
    *,
    task_status: TaskStatus[AgentUiApplication] = TASK_STATUS_IGNORED,
) -> None:
    async with open_application(settings) as application:
        task_status.started(application)
        await shutdown.wait()
    close_completed.set()


async def _hold_application(
    settings: AgentUiSettings,
    *,
    task_status: TaskStatus[AgentUiApplication] = TASK_STATUS_IGNORED,
) -> None:
    async with open_application(settings) as application:
        task_status.started(application)
        await sleep_forever()


def _comparable(value: datetime) -> datetime:
    return value.replace(tzinfo=None)

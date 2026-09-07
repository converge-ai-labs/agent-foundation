from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_harness_ui.errors import StoreConflictError
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import AgentResourceSource, ObjectKind, ObjectRef, ThreadConfiguration, open_local_store

pytestmark = pytest.mark.anyio


def _configuration(project_id: str = "project-main") -> ThreadConfiguration:
    return ThreadConfiguration(
        version=1,
        project_id=project_id,
        agent_source=AgentResourceSource(id="agent-main"),
        environment_profile_id="environment-native",
    )


def _initial() -> ObjectRef:
    return ObjectRef(
        object_kind=ObjectKind.thread_initial_state,
        object_schema_version="1",
        logical_digest="1" * 64,
    )


async def test_thread_metadata_uses_independent_compare_and_select_head(tmp_path: Path) -> None:
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        created = await store.threads.create(
            thread_id="thread-1",
            configuration=_configuration(),
            initial_state=_initial(),
            title="Initial",
        )
        assert created.metadata_version == 1

        updated = await store.threads.update_metadata(
            thread_id="thread-1",
            expected_version=1,
            title="Renamed",
            archived=True,
        )
        assert updated.metadata_version == 2
        assert updated.configuration.version == 1
        assert updated.title == "Renamed"
        assert updated.archived

        with pytest.raises(StoreConflictError) as stale:
            await store.threads.update_metadata(
                thread_id="thread-1",
                expected_version=1,
                title=None,
                archived=False,
            )
        assert stale.value.code == "thread_metadata_conflict"

        unchanged = await store.threads.update_metadata(
            thread_id="thread-1",
            expected_version=2,
            title="Renamed",
            archived=True,
        )
        assert unchanged.metadata_version == 2


async def test_thread_repository_keyset_is_stable_when_newer_threads_arrive(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        for index in range(3):
            await store.threads.create(
                thread_id=f"thread-{index}",
                configuration=_configuration(),
                initial_state=_initial(),
                created_at=start + timedelta(seconds=index),
            )

        first, total = await store.threads.list(limit=2)
        assert [item.thread_id for item in first] == ["thread-2", "thread-1"]
        assert total == 3

        await store.threads.create(
            thread_id="thread-new",
            configuration=_configuration(),
            initial_state=_initial(),
            created_at=start + timedelta(seconds=10),
        )
        second, total = await store.threads.list(
            before=(first[-1].updated_at, first[-1].thread_id),
            limit=2,
        )
        assert [item.thread_id for item in second] == ["thread-0"]
        assert total == 4


async def test_child_execution_repository_has_parent_scoped_keyset_pages(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    composition = ObjectRef(
        object_kind=ObjectKind.run_composition,
        object_schema_version="1",
        logical_digest="2" * 64,
    )
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        await store.threads.create(
            thread_id="thread-parent",
            configuration=_configuration(),
            initial_state=_initial(),
            created_at=start,
        )
        for index in range(3):
            child_thread_id = f"thread-child-{index}"
            await store.threads.create(
                thread_id=child_thread_id,
                parent_thread_id="thread-parent",
                configuration=_configuration(),
                initial_state=_initial(),
                created_at=start + timedelta(seconds=index),
            )
            await store.child_executions.create(
                execution_id=f"execution-{index}",
                parent_thread_id="thread-parent",
                child_thread_id=child_thread_id,
                child_run_id=f"run-{index}",
                run_composition=composition,
                created_at=start + timedelta(seconds=index),
            )

        first, total = await store.child_executions.page_for_parent("thread-parent", limit=2)
        second, _ = await store.child_executions.page_for_parent(
            "thread-parent",
            after=(first[-1].created_at, first[-1].execution_id),
            limit=2,
        )

    assert total == 3
    assert [item.execution_id for item in first] == ["execution-0", "execution-1"]
    assert [item.execution_id for item in second] == ["execution-2"]


async def test_project_recency_aggregates_all_non_archived_root_and_child_threads(tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        await store.threads.create(
            thread_id="thread-root",
            configuration=_configuration("project-a"),
            initial_state=_initial(),
            created_at=start,
        )
        await store.threads.create(
            thread_id="thread-child",
            parent_thread_id="thread-root",
            configuration=_configuration("project-a"),
            initial_state=_initial(),
            created_at=start + timedelta(seconds=5),
        )
        archived = await store.threads.create(
            thread_id="thread-archived",
            configuration=_configuration("project-b"),
            initial_state=_initial(),
            created_at=start + timedelta(seconds=10),
        )
        await store.threads.update_metadata(
            thread_id=archived.thread_id,
            expected_version=archived.metadata_version,
            title=None,
            archived=True,
            updated_at=start + timedelta(seconds=11),
        )

        recency = await store.threads.project_recency()

    assert recency == {"project-a": start + timedelta(seconds=5)}

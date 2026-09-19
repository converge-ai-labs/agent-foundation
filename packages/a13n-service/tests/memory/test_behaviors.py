from __future__ import annotations

import pytest
from a13n_service.interactions.errors import RunAcceptanceError
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.records import run_record
from a13n_service.memory.behaviors import RunMemorySelectionRecord
from a13n_service.storage import short_session, transaction
from tests.interactions.test_attempt_execution import _accept_root
from tests.interactions.test_queue import _fail_current_run
from tests.memory.selection_support import ordinary_memory
from tests.sql_capture import capture_sql

pytestmark = pytest.mark.anyio


async def test_acceptance_persists_memory_selection_without_reading_it_back(
    interaction_sessions, interaction_object_store
):
    with capture_sql(interaction_sessions) as statements:
        _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)

    selection_reads = [sql for sql in statements if sql.startswith("SELECT") and "FROM run_memory_selections" in sql]
    assert len(selection_reads) == 1
    async with short_session(interaction_sessions) as session:
        selection = await session.get(RunMemorySelectionRecord, run.id)
        assert selection is not None
        assert (selection.behavior_key, selection.binding_schema_version) == ("agent", 1)


@pytest.mark.parametrize("key,version", [("uninstalled", 1), ("agent", 2)])
async def test_finalization_rejects_an_unavailable_existing_selection(
    interaction_sessions, interaction_object_store, key, version
):
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    registry = ordinary_memory(interaction_sessions)
    async with transaction(interaction_sessions) as session:
        selection = await session.get(RunMemorySelectionRecord, run.id)
        selection.behavior_key = key
        selection.binding_schema_version = version

    with pytest.raises(RunAcceptanceError) as caught:
        async with transaction(interaction_sessions) as session:
            await registry.finalize(session, run, source_run_id=None)
    assert caught.value.code == "memory_binding_unavailable"
    async with short_session(interaction_sessions) as session:
        selection = await session.get(RunMemorySelectionRecord, run.id)
        assert (selection.behavior_key, selection.binding_schema_version) == (key, version)


@pytest.mark.parametrize("inherit", [False, True])
async def test_new_selection_still_validates_behavior_binding_and_rolls_back(
    interaction_sessions, interaction_object_store, monkeypatch, inherit
):
    _, source, _ = await _accept_root(interaction_sessions, interaction_object_store)
    await _fail_current_run(interaction_sessions, run_id=source.id, thread_id=source.thread_id)
    target = source.model_copy(update={"id": "run_memory_candidate", "idempotency_key": None})
    registry = ordinary_memory(interaction_sessions)
    validated = []

    async def validate_binding(session, run_id):
        selection = await session.get(RunMemorySelectionRecord, run_id)
        assert selection is not None and selection not in session.new
        assert (selection.behavior_key, selection.binding_schema_version) == ("agent", 1)
        validated.append(run_id)
        if run_id == target.id:
            raise RunAcceptanceError("memory_binding_missing", "Application binding is missing")

    monkeypatch.setattr(registry.default, "validate", validate_binding)
    with pytest.raises(RunAcceptanceError, match="Application binding is missing"):
        async with transaction(interaction_sessions) as session:
            session.add(run_record(target))
            await session.flush()
            await registry.finalize(session, target, source_run_id=source.id if inherit else None)

    assert validated == ([source.id, target.id] if inherit else [target.id])
    async with short_session(interaction_sessions) as session:
        assert await session.get(RunRecord, target.id) is None
        assert await session.get(RunMemorySelectionRecord, target.id) is None
        assert await session.get(RunMemorySelectionRecord, source.id) is not None

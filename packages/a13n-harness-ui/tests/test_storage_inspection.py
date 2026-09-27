"""Atomic inspection replacement without rewriting an unchanged transcript prefix."""

from datetime import UTC, datetime

import pytest
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.database import open_database, short_session, transaction
from a13n_harness_ui.storage.inspection import InspectionData, InspectionRepository, InspectionTurn
from a13n_harness_ui.storage.models import ThreadRecord
from anyio import create_task_group
from sqlalchemy import event, text

pytestmark = pytest.mark.anyio
_THREAD = "thr_inspection"
_INITIAL = "initial:" + "a" * 64
_NEXT = "b" * 64


@pytest.fixture
async def database(tmp_path):
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        now = datetime.now(UTC)
        async with transaction(database.sessions) as session:
            session.add(
                ThreadRecord(
                    thread_id=_THREAD,
                    created_at=now,
                    updated_at=now,
                    initial_state_schema_version="1",
                    initial_state_digest="a" * 64,
                )
            )
        yield database


async def select_source(database, source_id):
    async with transaction(database.sessions) as session:
        row = await session.get(ThreadRecord, _THREAD)
        row.continuation_digest = source_id
        row.continuation_schema_version = "1"


async def changes(database):
    async with short_session(database.sessions) as session:
        return await session.scalar(text("SELECT total_changes()"))


async def test_inspection_replacement_writes_only_changes_and_repeated_publication_is_noop(database):
    repository = InspectionRepository(database.sessions)
    first = InspectionData("{}", ("first", "second"), (InspectionTurn("turn-1", 0, 2, "old turn"),))
    assert await repository.publish(_THREAD, _INITIAL, first)
    await select_source(database, _NEXT)
    latest = InspectionData("{}", (*first.entries, "third"), (InspectionTurn("turn-1", 0, 3, "new turn"),))
    before = await changes(database)
    assert await repository.publish(_THREAD, _NEXT, latest)
    assert await changes(database) - before == 3  # Header, one new message, one changed turn.
    assert await repository.entries(_THREAD, _NEXT, (0, 1, 2)) == latest.entries
    assert await repository.turns(_THREAD, _NEXT) == ("new turn",)
    before = await changes(database)
    assert await repository.publish(_THREAD, _NEXT, latest)
    assert await changes(database) == before
    assert not await repository.publish(_THREAD, _INITIAL, first)
    assert await changes(database) == before


async def test_inspection_replacement_handles_edits_truncation_and_version_rebuild(database, monkeypatch):
    from a13n_harness_ui.storage import inspection

    repository = InspectionRepository(database.sessions)
    first = InspectionData(
        "{}",
        ("first", "second", "third"),
        (InspectionTurn("turn-1", 0, 1, "old one"), InspectionTurn("turn-2", 1, 3, "old two")),
    )
    assert await repository.publish(_THREAD, _INITIAL, first)
    await select_source(database, _NEXT)
    latest = InspectionData("{}", ("replacement",), (InspectionTurn("turn-new", 0, 1, "new"),))
    assert await repository.publish(_THREAD, _NEXT, latest)
    async with short_session(database.sessions) as session:
        assert (await session.execute(text("SELECT position, value_json FROM transcript_entry"))).all() == [
            (0, "replacement")
        ]
        assert (await session.execute(text("SELECT position, turn_id FROM transcript_turn"))).all() == [(0, "turn-new")]
    monkeypatch.setattr(inspection, "INSPECTION_VERSION", inspection.INSPECTION_VERSION + 1)
    assert await repository.header(_THREAD, _NEXT) is None
    rebuilt = InspectionData('{"rebuilt":true}', ("new encoding",), ())
    assert await repository.publish(_THREAD, _NEXT, rebuilt)
    assert (await repository.header(_THREAD, _NEXT)).metadata_json == rebuilt.metadata_json
    assert await repository.entries(_THREAD, _NEXT, (0,)) == rebuilt.entries
    assert await repository.turns(_THREAD, _NEXT) == ()


async def test_inspection_partial_replacement_rolls_back_header_and_rows(database):
    repository = InspectionRepository(database.sessions)
    first = InspectionData("{}", ("first",), (InspectionTurn("turn-1", 0, 1, "old"),))
    assert await repository.publish(_THREAD, _INITIAL, first)
    await select_source(database, _NEXT)
    batches = 0

    def fail_second_batch(conn, cursor, statement, parameters, context, executemany):
        nonlocal batches
        if statement.startswith("INSERT INTO transcript_entry"):
            batches += 1
            if batches == 2:
                raise RuntimeError("injected write failure")

    event.listen(database.engine.sync_engine, "before_cursor_execute", fail_second_batch)
    try:
        with pytest.raises(RuntimeError, match="injected write failure"):
            await repository.publish(_THREAD, _NEXT, InspectionData("{}", ("new",) * 101, ()))
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", fail_second_batch)
    assert await repository.header(_THREAD, _NEXT) is None
    assert await repository.entries(_THREAD, _INITIAL, (0,)) == first.entries
    assert await repository.turns(_THREAD, _INITIAL) == ("old",)
    assert await repository.publish(_THREAD, _NEXT, InspectionData("{}", ("recovered",), ()))


async def test_independent_publishers_recheck_index_under_the_writer_lock(database, tmp_path):
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as other:
        statements = []

        def observe(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO transcript_entry"):
                statements.append(statement)

        event.listen(database.engine.sync_engine, "before_cursor_execute", observe)
        event.listen(other.engine.sync_engine, "before_cursor_execute", observe)
        first, second = InspectionRepository(database.sessions), InspectionRepository(other.sessions)
        data = InspectionData("{}", ("entry",) * 101, ())
        async with create_task_group() as tasks:
            tasks.start_soon(first.publish, _THREAD, _INITIAL, data)
            tasks.start_soon(second.publish, _THREAD, _INITIAL, data)
        assert len(statements) == 2  # Two bounded batches, not two full publications.
        assert (await first.header(_THREAD, _INITIAL)).message_count == 101
        assert (await second.header(_THREAD, _INITIAL)).message_count == 101

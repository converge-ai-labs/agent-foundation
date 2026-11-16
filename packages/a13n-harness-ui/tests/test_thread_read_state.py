"""Fixed-size shared read state, fenced against newer results and manual marks."""

from contextlib import asynccontextmanager

import httpx
import pytest
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import ObjectKind, ObjectRef, open_local_store
from a13n_harness_ui.storage.contracts import ThreadReadModel, ThreadReadReceipt
from sqlalchemy import event, text

from .test_app import _write_configuration
from .test_configuration_protocol import HEADERS
from .test_interactive_protocol import listener
from .test_thread_repository import _configuration, _initial

pytestmark = pytest.mark.anyio


def receipt(thread):
    return ThreadReadReceipt(
        thread_id=thread.thread_id,
        read_state_version=thread.read_state_version,
        completion_version=0 if thread.completion is None else thread.completion.version,
    )


async def test_completion_manual_marks_and_stale_reads_share_one_fixed_head(tmp_path):
    settings = StorageSettings(data_root=tmp_path)
    ref = ObjectRef(object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest="a" * 64)
    async with open_local_store(settings) as store:
        initial = await store.threads.create(
            thread_id="thread-one", configuration=_configuration(), initial_state=_initial()
        )
        assert not initial.unread and initial.read_state_version == 0
        completed = await store.threads.select_continuation(
            thread_id=initial.thread_id,
            expected=None,
            replacement=ref,
            read_model=ThreadReadModel(),
            completed_run_id="run-first",
        )
        assert completed.unread and completed.read_state_version == 1
        assert await store.threads.acknowledge_read((receipt(initial),)) == ()
        assert await store.threads.acknowledge_read((receipt(completed),)) == (initial.thread_id,)
        read = await store.threads.get(initial.thread_id)
        assert not read.unread and read.read_state_version == 2
        repeated = await store.threads.select_continuation(
            thread_id=initial.thread_id,
            expected=ref,
            replacement=ref,
            read_model=ThreadReadModel(),
            completed_run_id="run-first",
        )
        assert not repeated.unread and repeated.read_state_version == 2
        await store.threads.mark_unread(initial.thread_id)
        marked = await store.threads.get(initial.thread_id)
        assert marked.unread and marked.read_state_version == 3
        await store.threads.mark_unread(initial.thread_id)
        assert await store.threads.acknowledge_read((receipt(marked),)) == ()
        marked = await store.threads.get(initial.thread_id)
        assert marked.read_state_version == 4
        assert marked.metadata_version == repeated.metadata_version
        assert marked.updated_at == repeated.updated_at
        assert marked.touched_at == repeated.touched_at
        assert marked.activity_at == repeated.activity_at
        later = await store.threads.select_continuation(
            thread_id=initial.thread_id,
            expected=ref,
            replacement=ref,
            read_model=ThreadReadModel(),
            completed_run_id="run-next",
        )
        assert later.unread and later.read_state_version == 5 and later.completion.version == 2
        assert await store.threads.acknowledge_read((receipt(marked),)) == ()
    async with open_local_store(settings) as store:
        saved = await store.threads.get(initial.thread_id)
        assert saved.unread and saved.read_state_version == 5
        assert await store.threads.acknowledge_read((receipt(saved),)) == (initial.thread_id,)
    async with open_local_store(settings) as store:
        saved = await store.threads.get(initial.thread_id)
        assert not saved.unread and saved.read_state_version == 6


async def test_duplicate_reads_never_enter_writer_and_batches_use_one_update(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        originals = [
            await store.threads.create(
                thread_id=f"thread-{i}", configuration=_configuration(), initial_state=_initial()
            )
            for i in range(100)
        ]
        for thread in originals:
            await store.threads.mark_unread(thread.thread_id)
        rows, _ = await store.threads.list(limit=100)
        receipts = tuple(receipt(row) for row in rows)
        statements = []

        def executed(connection, cursor, statement, parameters, context, many):
            statements.append(statement)

        event.listen(store.database.engine.sync_engine, "before_cursor_execute", executed)
        try:
            assert len(await store.threads.acknowledge_read(receipts)) == 100
            assert sum(statement.startswith("UPDATE thread ") for statement in statements) == 1
            statements.clear()
            for _ in range(30):
                assert await store.threads.acknowledge_read(receipts) == ()
            assert not any(statement.startswith(("UPDATE", "INSERT", "BEGIN IMMEDIATE")) for statement in statements)
            assert len(statements) == 30
        finally:
            event.remove(store.database.engine.sync_engine, "before_cursor_execute", executed)
        async with store.database.engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM thread")) == 100
            plan = (
                await connection.execute(
                    text(
                        "EXPLAIN QUERY PLAN SELECT thread_id FROM thread WHERE unread = 1 AND memory_scope IS NULL AND parent_thread_id IS NULL AND archived IS 0 ORDER BY updated_at DESC, thread_id DESC LIMIT 100"
                    )
                )
            ).all()
            assert "ix_thread_unread" in str(plan)


async def test_read_preflight_race_is_fenced_inside_update(tmp_path, monkeypatch):
    import a13n_harness_ui.storage.repositories as repositories

    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        initial = await store.threads.create(
            thread_id="thread-race", configuration=_configuration(), initial_state=_initial()
        )
        await store.threads.mark_unread(initial.thread_id)
        observed = await store.threads.get(initial.thread_id)
        original = repositories.transaction

        @asynccontextmanager
        async def racing_transaction(sessions):
            monkeypatch.setattr(repositories, "transaction", original)
            await store.threads.mark_unread(initial.thread_id)
            async with original(sessions) as session:
                yield session

        monkeypatch.setattr(repositories, "transaction", racing_transaction)
        assert await store.threads.acknowledge_read((receipt(observed),)) == ()
        current = await store.threads.get(initial.thread_id)
        assert current.unread and current.read_state_version == observed.read_state_version + 1


async def test_http_two_devices_discovery_bulk_and_restart(tmp_path):
    configuration = _write_configuration(tmp_path)
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with (
            httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as first,
            httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as second,
        ):
            rows = [(await first.post("/api/threads", json={"title": f"Saved {i}"})).json() for i in range(8)]
            marked = []
            for row in rows:
                response = await first.post(f"/api/threads/{row['thread_id']}/unread")
                assert response.status_code == 200
                marked.append(response.json())
            first_page = (await second.get("/api/threads", params={"unread": "true", "limit": 5})).json()
            assert first_page["total"] == 8 and len(first_page["threads"]) == 5
            tail = (
                await second.get(
                    "/api/threads", params={"unread": "true", "limit": 5, "cursor": first_page["next_cursor"]}
                )
            ).json()
            assert len(tail["threads"]) == 3
            body = {
                "threads": [
                    {
                        "thread_id": row["thread_id"],
                        "read_state_version": row["read_state_version"],
                        "completion_version": 0,
                    }
                    for row in marked
                ]
            }
            # One device marks again after the other captured its bulk action.
            await first.post(f"/api/threads/{rows[0]['thread_id']}/unread")
            read = await second.post("/api/threads/read", json=body)
            assert read.status_code == 200
            assert sum(row["unread"] for row in read.json()["threads"]) == 1
            assert (await second.post("/api/threads/read", json={"threads": body["threads"] * 13})).status_code == 400
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            page = (await api.get("/api/threads", params={"unread": "true"})).json()
            assert [row["thread_id"] for row in page["threads"]] == [rows[0]["thread_id"]]
            assert (await api.get(f"/api/threads/{rows[1]['thread_id']}")).json()["thread"]["unread"] is False


def test_upgrade_keeps_historical_completions_read_and_preserves_children(tmp_path):
    from a13n_harness_ui.storage.migration import DatabaseMigrator
    from alembic import command
    from sqlalchemy import Engine, create_engine

    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "f196302d868f"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO thread (thread_id, parent_thread_id, metadata_version, archived, created_at, updated_at, initial_state_schema_version, initial_state_digest, completion_version) VALUES (:id, :parent, 1, 0, '2026-01-01', '2026-01-01', '1', :digest, :version)"
                ),
                [
                    {"id": "root", "parent": None, "digest": "a" * 64, "version": 5},
                    {"id": "child", "parent": "root", "digest": "b" * 64, "version": 0},
                ],
            )
        statements = []

        def executed(connection, cursor, statement, parameters, context, many):
            statements.append(" ".join(statement.split()))

        event.listen(Engine, "before_cursor_execute", executed)
        try:
            migrator.upgrade()
        finally:
            event.remove(Engine, "before_cursor_execute", executed)
        assert sum(statement.startswith("ALTER TABLE thread ADD COLUMN ") for statement in statements) == 2
        assert not any("_alembic_tmp_thread " in statement for statement in statements)
        migrator.verify_current()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT unread, read_state_version FROM thread")).all() == [(0, 0), (0, 0)]
            assert (
                connection.execute(text("SELECT completion_version FROM thread WHERE thread_id='root'")).scalar_one()
                == 5
            )
            assert (
                connection.execute(text("SELECT parent_thread_id FROM thread WHERE thread_id='child'")).scalar_one()
                == "root"
            )
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()


def test_shared_read_upgrade_rolls_back_native_columns_and_retries(tmp_path):
    from a13n_harness_ui.storage.migration import DatabaseMigrator
    from alembic import command
    from sqlalchemy import Engine, create_engine, inspect

    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "f196302d868f"), write=True)

    def interrupted(connection, cursor, statement, parameters, context, many):
        if statement.startswith("CREATE INDEX ix_thread_unread "):
            raise RuntimeError("Interrupted before unread index creation")

    event.listen(Engine, "before_cursor_execute", interrupted)
    try:
        with pytest.raises(RuntimeError, match="Interrupted before unread index creation"):
            migrator.upgrade()
    finally:
        event.remove(Engine, "before_cursor_execute", interrupted)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "f196302d868f"
            assert not {"unread", "read_state_version"} & {
                column["name"] for column in inspect(connection).get_columns("thread")
            }
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()

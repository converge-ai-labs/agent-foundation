from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_harness.compaction import CompactionOperation, CompactionResult
from a13n_harness_ui.compaction import BackgroundCompaction
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.compaction import CompactionRepository, CompactionSettlement, settle_compaction
from a13n_harness_ui.storage.database import open_database, short_session, transaction
from a13n_harness_ui.storage.models import CompactionRecord
from sqlalchemy import select, update

from .test_background_compaction import _input, _summary

pytestmark = pytest.mark.anyio


async def _age(database, operation_id: str) -> None:
    async with transaction(database.sessions) as session:
        await session.execute(
            update(CompactionRecord)
            .where(CompactionRecord.operation_id == operation_id)
            .values(created_at=datetime.now(UTC) - timedelta(days=8))
        )


@pytest.mark.parametrize("status", ["running", "ready", "failed"])
async def test_expiration_fences_publication_and_allows_new_work(tmp_path: Path, status: str):
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner = BackgroundCompaction(repository, tmp_path)
        input = _input()
        operation = CompactionOperation(input=input, status="running")
        await repository.admit(operation)
        if status == "ready":
            operation = CompactionOperation(
                input=input, status="ready", result=CompactionResult(basis=input.basis, summary="summary")
            )
            await repository.finish(operation)
        elif status == "failed":
            operation = CompactionOperation(input=input, status="failed", error_code="compaction_failed")
            await repository.finish(operation)
        await _age(database, input.basis.operation_id)
        cutoff = datetime.now(UTC) - timedelta(days=7)
        await repository.expire(input.basis.operation_id, cutoff)
        assert await repository.get(input.basis.thread_id) is None
        async with short_session(database.sessions) as session:
            row = await session.get(CompactionRecord, input.basis.operation_id)
            assert row is not None and row.disposition == "expired" and row.settled_at is not None
            assert row.status == status
        # Worker completion cannot resurrect settled work, even before pruning.
        assert not await repository.finish(CompactionOperation(input=input, status="failed", error_code="late"))
        next_input = _input("compact-next")
        next_operation = await owner.submit(next_input, _summary)
        assert next_operation.input.basis == next_input.basis
        assert await repository.prune(limit=1) == 1
        assert await repository.prune(limit=1) == 0
        assert await repository.get(input.basis.thread_id) is not None
        await owner.close()


async def test_pruner_skips_live_users_in_other_apps_and_removes_stale_holders(tmp_path: Path):
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        first, second, janitor = (BackgroundCompaction(repository, tmp_path) for _ in range(3))
        input = _input()
        await first.submit(input, _summary)
        ready = await first.wait(input.basis.thread_id, input.basis.operation_id)
        await _age(database, input.basis.operation_id)
        async with first.use(input.basis.thread_id):
            async with second.use(input.basis.thread_id):
                await janitor.prune(604800)
                assert await repository.get(input.basis.thread_id) == ready
            await janitor.prune(604800)
            assert await repository.get(input.basis.thread_id) == ready
        # Process death leaves a path but releases the OS lock.
        stale = first._use_directory(input.basis.thread_id) / "dead.lock"
        stale.touch()
        await janitor.prune(604800)
        assert not stale.exists()
        assert await first.inspect(input.basis.thread_id) is None
        assert await second.inspect(input.basis.thread_id) is None
        async with short_session(database.sessions) as session:
            assert await session.get(CompactionRecord, input.basis.operation_id) is None
        await asyncio.gather(first.close(), second.close(), janitor.close())


async def test_pruner_preserves_live_generation_and_pending_publication(tmp_path: Path, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def request(input):
        started.set()
        await release.wait()
        return await _summary(input)

    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner, janitor = BackgroundCompaction(repository, tmp_path), BackgroundCompaction(repository, tmp_path)
        input = _input()
        await owner.submit(input, request)
        await started.wait()
        await _age(database, input.basis.operation_id)
        await janitor.prune(604800)
        assert await repository.get(input.basis.thread_id) is not None
        finish = repository.finish

        async def unavailable(operation):
            raise OSError("unavailable")

        monkeypatch.setattr(repository, "finish", unavailable)
        release.set()
        await asyncio.gather(*owner._tasks.values())
        assert owner._pending and not owner._tasks
        await janitor.prune(604800)
        assert await repository.get(input.basis.thread_id) is not None
        monkeypatch.setattr(repository, "finish", finish)
        assert (await owner.inspect(input.basis.thread_id)).status == "ready"
        await janitor.prune(604800)
        assert await owner.inspect(input.basis.thread_id) is None
        await asyncio.gather(owner.close(), janitor.close())


async def test_expiration_and_consumer_registration_share_gate(tmp_path: Path, monkeypatch):
    expiring, release, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        owner, janitor = BackgroundCompaction(repository, tmp_path), BackgroundCompaction(repository, tmp_path)
        input = _input()
        await repository.admit(CompactionOperation(input=input, status="failed", error_code="old"))
        await _age(database, input.basis.operation_id)
        expire = repository.expire

        async def blocked(*args):
            expiring.set()
            await release.wait()
            await expire(*args)

        monkeypatch.setattr(repository, "expire", blocked)
        pruning = asyncio.create_task(janitor.prune(604800))
        await expiring.wait()

        async def consumer():
            async with owner.use(input.basis.thread_id):
                entered.set()
                assert await owner.inspect(input.basis.thread_id) is None

        consuming = asyncio.create_task(consumer())
        await asyncio.sleep(0)
        assert not entered.is_set()
        release.set()
        await asyncio.gather(pruning, consuming)
        assert entered.is_set()
        await asyncio.gather(owner.close(), janitor.close())


async def test_cancelled_consumer_releases_pin(tmp_path: Path):
    entered = asyncio.Event()
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        owner = BackgroundCompaction(CompactionRepository(database.sessions), tmp_path)

        async def consumer():
            async with owner.use("thread_test"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(consumer())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not owner._has_users("thread_test")
        await owner.close()


async def test_pruning_is_bounded_and_does_not_remove_recent_unsettled_work(tmp_path: Path):
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = CompactionRepository(database.sessions)
        for index in range(3):
            input = _input(f"compact-{index}")
            await repository.admit(CompactionOperation(input=input, status="failed", error_code="failed"))
            async with transaction(database.sessions) as session:
                await settle_compaction(
                    session, input.basis.thread_id, settlement=CompactionSettlement(input.basis.operation_id, "skipped")
                )
        latest = await repository.admit(CompactionOperation(input=_input("compact-recent"), status="running"))
        assert await repository.prune(limit=2) == 2
        async with short_session(database.sessions) as session:
            assert len((await session.scalars(select(CompactionRecord))).all()) == 2
        owner = BackgroundCompaction(repository, tmp_path)
        await owner.prune(604800)
        assert await repository.get(latest.input.basis.thread_id) == latest
        assert await repository.prune() == 0
        await owner.close()


@pytest.mark.parametrize("disposition", ["applied", "skipped", "cleared"])
async def test_checkpoint_settlement_commits_and_rolls_back_with_selection(tmp_path: Path, monkeypatch, disposition):
    from a13n_harness_ui.errors import StoreConflictError
    from a13n_harness_ui.storage import open_local_store
    from a13n_harness_ui.storage.contracts import ThreadReadModel
    from sqlalchemy.ext.asyncio import AsyncSession

    from .test_storage_work import continuation
    from .test_thread_repository import _configuration, _initial

    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        input = _input()
        thread = await store.threads.create(
            thread_id=input.basis.thread_id, configuration=_configuration(), initial_state=_initial()
        )
        operation = await store.compactions.admit(CompactionOperation(input=input, status="running"))
        first, second = continuation("a"), continuation("b")
        await store.threads.select_continuation(
            thread_id=thread.thread_id, expected=None, replacement=first, read_model=ThreadReadModel()
        )
        settlement = None if disposition == "cleared" else CompactionSettlement(input.basis.operation_id, disposition)

        async def select(expected):
            return await store.threads.select_continuation(
                thread_id=thread.thread_id,
                expected=expected,
                replacement=second,
                read_model=ThreadReadModel(),
                compaction_settlement=settlement,
                clear_compaction=disposition == "cleared",
            )

        with pytest.raises(StoreConflictError):
            await select(None)
        assert await store.compactions.get(thread.thread_id) == operation

        async def fail_flush(self, objects=None):
            # The UPDATE ran inside the transaction, but is not yet durable.
            row = await self.get(CompactionRecord, input.basis.operation_id)
            assert row is not None and row.disposition == disposition
            raise OSError("checkpoint flush failed")

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "flush", fail_flush)
            with pytest.raises(OSError, match="checkpoint flush failed"):
                await select(first)
        assert (await store.threads.get(thread.thread_id)).continuation == first
        assert await store.compactions.get(thread.thread_id) == operation
        await select(first)
        assert (await store.threads.get(thread.thread_id)).continuation == second
        assert await store.compactions.get(thread.thread_id) is None
        async with short_session(store.database.sessions) as session:
            row = await session.get(CompactionRecord, input.basis.operation_id)
            assert row is not None and row.disposition == disposition and row.settled_at is not None
        assert not await store.compactions.finish(CompactionOperation(input=input, status="failed", error_code="late"))
        # A stale marker cannot settle the next admission on the same Thread.
        next_operation = await store.compactions.admit(
            CompactionOperation(input=_input("compact-next"), status="running")
        )
        async with transaction(store.database.sessions) as session:
            await settle_compaction(
                session, thread.thread_id, settlement=CompactionSettlement(input.basis.operation_id, "skipped")
            )
        assert await store.compactions.get(thread.thread_id) == next_operation
        assert await store.compactions.prune() == 1
        assert (await store.threads.get(thread.thread_id)).continuation == second

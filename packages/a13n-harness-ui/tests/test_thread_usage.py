from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from a13n_harness import HarnessRunResult, HarnessRunResultEvent
from a13n_harness.events import HarnessEvent, HarnessExtensionEvent, UsageReportPayload
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, ProviderUsage, ProviderUsageRecord
from a13n_harness_ui.errors import StoreIntegrityError
from a13n_harness_ui.interactive.usage import thread_usage_text
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.database import open_database, transaction
from a13n_harness_ui.storage.migration import DatabaseMigrator
from a13n_harness_ui.storage.models import ThreadRecord
from a13n_harness_ui.storage.usage import ThreadUsageRepository
from alembic import command
from pydantic_ai.usage import RunUsage
from sqlalchemy import create_engine, select

pytestmark = pytest.mark.anyio
_NOW = datetime(2026, 9, 7, tzinfo=UTC)


def _thread(name: str, parent: str | None = None) -> ThreadRecord:
    return ThreadRecord(
        thread_id=name,
        parent_thread_id=parent,
        created_at=_NOW,
        updated_at=_NOW,
        initial_state_schema_version="1",
        initial_state_digest="a" * 64,
    )


def _model(index: int, *, run: str = "run-root", child: bool = False, cost: Decimal | None = None) -> ModelUsageRecord:
    return ModelUsageRecord(
        record_id=f"usage-{run}-{index}",
        run_id=run,
        response_ordinal=index,
        agent_instance_id=f"agent-{run}",
        parent_agent_instance_id="agent-root" if child else None,
        response_state="complete",
        model_name="test-model",
        provider_name="test-provider",
        response_timestamp=_NOW,
        request_usage=BoundedRequestUsage(
            input_tokens=100, output_tokens=20, cache_read_tokens=60, input_audio_tokens=5, cost=cost
        ),
        cost_source="unknown" if cost is None else "custom",
        pricing_status="disabled" if cost is None else "applied",
    )


def _receipt(
    *, run: str = "run-root", cost: Decimal | None = Decimal("0.2"), currency: str | None = "EUR"
) -> ProviderUsageRecord:
    return ProviderUsageRecord(
        record_id="usage-receipt",
        run_id=run,
        ordinal=0,
        agent_instance_id=f"agent-{run}",
        source="test",
        usage=ProviderUsage(
            usage_id="receipt-one",
            provider="test-provider",
            product="test-product",
            timestamp=_NOW,
            cost=cost,
            currency=currency,
        ),
    )


def _report(*records: ModelUsageRecord | ProviderUsageRecord) -> HarnessEvent:
    return HarnessEvent(
        thread_id="thread-root",
        run_id="run-root",
        sequence=1,
        occurred_at=_NOW,
        event=HarnessExtensionEvent(
            kind="usage",
            payload=UsageReportPayload(
                report_id="usage-report",
                reason="model_request",
                chunk_index=0,
                chunk_count=2,
                records=tuple(record.model_dump(mode="json") for record in records),
            ).model_dump(mode="json"),
        ),
    )


async def test_reports_terminal_and_receipts_deduplicate_across_root_inline_and_async_runs(tmp_path: Path) -> None:
    settings = StorageSettings(data_root=tmp_path)
    path = tmp_path / "metadata.sqlite3"
    async with open_database(path, settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thread-root"))
            session.add(_thread("thread-other"))
            await session.flush()
            session.add(_thread("thread-child", "thread-root"))
            await session.flush()
            session.add(_thread("thread-grandchild", "thread-child"))
        repository = ThreadUsageRepository(database.sessions)
        record = _model(0, cost=Decimal("0.125"))
        report = _report(record, _model(0, run="run-inline", child=True), _receipt())
        await repository.observe(thread_id="thread-root", item=report)
        await repository.observe(thread_id="thread-root", item=report)
        await repository.append(
            thread_id="thread-child", records=(_model(0, run="run-async"), _receipt(run="run-async"))
        )
        await repository.append(thread_id="thread-grandchild", records=(_model(0, run="run-nested"),))
        await repository.append(thread_id="thread-other", records=(record, _receipt()))
        # Inclusive native totals are deliberately enormous: the Host must use only records.
        result = HarnessRunResult(
            thread_id="thread-root",
            run_id="run-root",
            status="cancelled",
            state=None,
            output=None,
            usage=RunUsage(input_tokens=999999),
            usage_records=(record, _receipt()),
        )
        await repository.observe(
            thread_id="thread-root",
            item=HarnessRunResultEvent(
                thread_id="thread-root",
                run_id="run-root",
                sequence=2,
                occurred_at=_NOW,
                result=result,
            ),
        )
        snapshot = await repository.snapshot(thread_id="thread-root")
        assert snapshot.root.model_requests == 1
        assert snapshot.descendants.model_requests == 3
        assert snapshot.combined.model_requests == 4
        assert dict(snapshot.combined.tokens)["input_tokens"] == 400
        assert dict(snapshot.combined.tokens)["cache_read_tokens"] == 240
        assert snapshot.combined.model_cost_usd == Decimal("0.125")
        assert snapshot.combined.unknown_model_costs == 3
        assert snapshot.combined.provider_receipts == 1
        assert snapshot.combined.provider_costs == (("EUR", Decimal("0.2")),)
        assert sum(run.totals.model_requests for run in snapshot.recent_runs) == 4
        assert {run.run_id for run in snapshot.recent_runs} == {"run-root", "run-inline", "run-async", "run-nested"}
        assert snapshot.recent_runs[0].run_id == "run-nested"
        assert (await repository.snapshot(thread_id="thread-other")).combined.model_requests == 1
        with pytest.raises(ValueError, match="root Threads"):
            await repository.snapshot(thread_id="thread-child")
        summary = thread_usage_text(snapshot)
        assert len(summary.splitlines()) < 12
        assert "Cache read   240 (50.0%)" in summary
        assert "Recorded so far" not in summary and "not a provider invoice" not in summary
        assert "of input + output" not in summary
        assert "Recent Runs" not in summary and "Audio:" not in summary
        assert "3 unknown-cost responses" in summary and "EUR 0.2" in summary
        assert "/usage details" in summary and "root 1 / children 3" in summary
        text = thread_usage_text(snapshot, details=True)
        assert "50.0%" in text and "0.125000 known subtotal" in text
        assert "3 unknown-cost responses" in text and "EUR 0.2" in text
        assert "Recent Runs" in text and "run-inline" in text
        assert "Not a provider invoice" in text
    async with open_database(path, settings) as database:
        restored = await ThreadUsageRepository(database.sessions).snapshot(thread_id="thread-root")
        assert restored == snapshot


async def test_conflicting_usage_is_not_overwritten_and_chunk_is_atomic(tmp_path: Path) -> None:
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thread-root"))
        repository = ThreadUsageRepository(database.sessions)
        await repository.append(thread_id="thread-root", records=(_model(0), _receipt()))
        with pytest.raises(StoreIntegrityError, match="different facts"):
            await repository.append(thread_id="thread-root", records=(_model(1), _model(0, cost=Decimal(1))))
        with pytest.raises(StoreIntegrityError, match="different facts"):
            await repository.append(thread_id="thread-root", records=(_receipt(run="other", cost=Decimal(1)),))
        snapshot = await repository.snapshot(thread_id="thread-root")
        assert snapshot.combined.model_requests == 1
        assert snapshot.combined.provider_costs == (("EUR", Decimal("0.2")),)


async def test_bounded_batches_keep_all_totals_and_recent_run_details(tmp_path: Path) -> None:
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thread-root"))
        repository = ThreadUsageRepository(database.sessions)
        empty = await repository.snapshot(thread_id="thread-root")
        assert empty.first_observed_at is None
        assert thread_usage_text(empty) == "Usage · this conversation\nNo recorded usage yet."
        assert "not proven zero" in thread_usage_text(empty, details=True)
        for batch in range(3):
            await repository.append(
                thread_id="thread-root",
                records=tuple(
                    _model(index, run=f"run-{index}").model_copy(update={"model_name": f"model-{index}"})
                    for index in range(batch * 100, (batch + 1) * 100)
                ),
            )
        snapshot = await repository.snapshot(thread_id="thread-root")
        assert snapshot.combined.model_requests == 300
        assert len(snapshot.models) == 32
        assert snapshot.other_models.model_requests == 268
        assert len(snapshot.recent_runs) == 32
        assert snapshot.recent_runs[0].run_id == "run-299"
        assert snapshot.other_runs.model_requests == 268
        assert (
            sum(run.totals.model_requests for run in snapshot.recent_runs) + snapshot.other_runs.model_requests == 300
        )


async def test_startup_automatically_upgrades_populated_previous_revision_and_is_repeatable(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "9ad1ce20a90f"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                ThreadRecord.__table__.insert().values(
                    thread_id="thread-existing",
                    title="Keep this",
                    archived=False,
                    metadata_version=1,
                    created_at=_NOW,
                    updated_at=_NOW,
                    initial_state_schema_version="1",
                    initial_state_digest="a" * 64,
                )
            )
    finally:
        engine.dispose()
    for _ in range(2):
        async with open_database(path, StorageSettings(data_root=tmp_path)) as database:
            async with transaction(database.sessions) as session:
                assert await session.scalar(select(ThreadRecord.title)) == "Keep this"
            repository = ThreadUsageRepository(database.sessions)
            await repository.append(thread_id="thread-existing", records=(_model(0),))
            assert (await repository.snapshot(thread_id="thread-existing")).combined.model_requests == 1
    migrator.verify_current()

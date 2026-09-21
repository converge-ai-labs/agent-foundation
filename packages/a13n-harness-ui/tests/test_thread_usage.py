from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from a13n_harness import HarnessRunResult, HarnessRunResultEvent
from a13n_harness.events import HarnessEvent, HarnessExtensionEvent, UsageReportPayload
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, ProviderUsage, ProviderUsageRecord, UsageMeasure
from a13n_harness_ui.errors import StoreIntegrityError
from a13n_harness_ui.interactive.usage import thread_usage_text
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.database import open_database, transaction
from a13n_harness_ui.storage.migration import DatabaseMigrator
from a13n_harness_ui.storage.models import ThreadRecord
from a13n_harness_ui.storage.usage import ThreadUsageRepository
from alembic import command
from pydantic_ai.usage import RunUsage
from sqlalchemy import MetaData, Table, create_engine, select

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
            measures=(UsageMeasure(unit="requests", quantity=Decimal(1)),),
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
                Table("thread", MetaData(), autoload_with=connection)
                .insert()
                .values(
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


async def test_usage_cache_reads_only_new_committed_suffix_and_survives_other_writers(tmp_path, monkeypatch):
    from a13n_harness_ui.storage.usage import _Aggregation

    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thread-root"))
        reader = ThreadUsageRepository(database.sessions)
        writer = ThreadUsageRepository(database.sessions)
        decoded = []
        add = _Aggregation.add

        def observed(self, sequence, descendant, payload, timestamp):
            decoded.append(sequence)
            add(self, sequence, descendant, payload, timestamp)

        monkeypatch.setattr(_Aggregation, "add", observed)
        await writer.append(thread_id="thread-root", records=(_model(0),))
        first = await reader.snapshot(thread_id="thread-root")
        assert len(decoded) == 1
        assert await reader.snapshot(thread_id="thread-root") == first
        assert len(decoded) == 1
        await writer.append(thread_id="thread-root", records=(_model(1),))
        assert (await reader.snapshot(thread_id="thread-root")).combined.model_requests == 2
        assert len(decoded) == 2
        # Group changes rebuild rather than subtracting capped currency buckets.
        await writer.append(thread_id="thread-root", records=(_model(0, run="run-new"),))
        cached = await reader.snapshot(thread_id="thread-root")
        assert cached.combined.model_requests == 3
        assert len(decoded) == 5
        assert cached == await ThreadUsageRepository(database.sessions).snapshot(thread_id="thread-root")


async def test_legacy_and_auxiliary_records_reaggregate_without_repricing_or_context_pollution(tmp_path: Path) -> None:
    import json

    from a13n_harness_ui.storage.models import ThreadUsageRecord

    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        legacy = _model(0).model_dump(mode="json")
        for field in (
            "source",
            "tool_id",
            "tool_call_id",
            "pricing_status",
            "cost_source",
            "pricing_revision",
            "pricing_rule_id",
        ):
            legacy.pop(field, None)
        async with transaction(database.sessions) as session:
            session.add(_thread("thread-root"))
            await session.flush()
            session.add(
                ThreadUsageRecord(
                    root_thread_id="thread-root",
                    origin_thread_id="thread-root",
                    record_id=legacy["record_id"],
                    run_id=legacy["run_id"],
                    descendant=False,
                    payload_json=json.dumps(legacy),
                    observed_at=_NOW,
                )
            )
        repository = ThreadUsageRepository(database.sessions)
        primary = _model(1, cost=Decimal("0.1")).model_copy(update={"model_name": "switched-model"})
        media = _model(2, cost=Decimal("0.2")).model_copy(
            update={
                "source": "files.media_understanding",
                "tool_id": "filesystem.view",
                "model_name": "media-model",
            }
        )
        child_media = _model(3, run="run-child", child=True, cost=Decimal("0.3")).model_copy(
            update={
                "source": "files.media_understanding",
                "model_name": "media-model",
            }
        )
        records = (primary, media, child_media, _receipt(cost=None, currency=None))
        await repository.append(thread_id="thread-root", records=records)
        await repository.append(thread_id="thread-root", records=records)
        # Old reports and new terminal reconciliation agree after additive defaults.
        await repository.append(thread_id="thread-root", records=(ModelUsageRecord.model_validate(legacy),))
        view = await repository.snapshot(thread_id="thread-root")
        assert view.combined.model_requests == 4
        assert view.combined.model_cost_usd == Decimal("0.6")
        assert view.combined.unknown_model_costs == 1
        assert view.combined.unknown_provider_costs == 1
        assert dict(view.combined.tokens)["input_tokens"] == 400
        model = next(row for row in view.model_scopes if row.name == "test-provider/media-model")
        assert model.root.model_cost_usd == Decimal("0.2")
        assert model.descendants.model_cost_usd == Decimal("0.3")
        assert model.combined.model_cost_usd == Decimal("0.5")
        assert sum(group.totals.model_requests for group in view.groups) == 4
        assert await repository.latest_root_request(thread_id="thread-root") == primary
        fresh = ThreadUsageRepository(database.sessions)
        assert await fresh.snapshot(thread_id="thread-root") == view
        assert "media-model" in thread_usage_text(view)
        assert "files.media_understanding" in thread_usage_text(view, details=True)


async def test_auxiliary_group_overflow_preserves_all_scope_totals(tmp_path: Path) -> None:
    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thread-root"))
        repository = ThreadUsageRepository(database.sessions)
        records = tuple(
            _model(index, child=index % 2 == 1).model_copy(
                update={
                    "model_name": f"model-{index}",
                    "source": "files.media_understanding",
                    "agent_instance_id": f"agent-{index}",
                }
            )
            for index in range(140)
        )
        for start in range(0, len(records), 128):
            await repository.append(thread_id="thread-root", records=records[start : start + 128])
        view = await repository.snapshot(thread_id="thread-root")
        assert len(view.models) == 32
        assert len(view.model_scopes) == 33
        assert len(view.groups) == 128
        assert view.other_groups is not None and view.other_groups.model_requests == 12
        assert sum(row.root.model_requests for row in view.model_scopes) == 70
        assert sum(row.descendants.model_requests for row in view.model_scopes) == 70
        assert view.combined.model_requests == 140
        assert await repository.latest_root_request(thread_id="thread-root") is None

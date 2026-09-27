from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from a13n_harness import HarnessRunResult, HarnessRunResultEvent
from a13n_harness.events import HarnessEvent, HarnessExtensionEvent, UsageReportPayload
from a13n_harness.usage import (
    BoundedRequestUsage,
    ModelUsageRecord,
    ProviderUsage,
    ProviderUsageRecord,
    RunUsageSummary,
    UsageMeasure,
    UsageSnapshot,
)
from a13n_harness_ui.errors import StoreIntegrityError
from a13n_harness_ui.interactive.usage import thread_usage_text
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.database import open_database, transaction
from a13n_harness_ui.storage.migration import DatabaseMigrator
from a13n_harness_ui.storage.models import ThreadRecord
from a13n_harness_ui.storage.usage import ThreadUsageRepository
from alembic import command
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
        call_id="call_fixture",
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
            schema_version="1",
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
            usage=RunUsageSummary(input_tokens=999999),
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


async def test_usage_projection_survives_other_writers(tmp_path):
    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thread-root"))
        reader = ThreadUsageRepository(database.sessions)
        writer = ThreadUsageRepository(database.sessions)
        await writer.append(thread_id="thread-root", records=(_model(0),))
        first = await reader.snapshot(thread_id="thread-root")
        assert await reader.snapshot(thread_id="thread-root") == first
        await writer.append(thread_id="thread-root", records=(_model(1),))
        assert (await reader.snapshot(thread_id="thread-root")).combined.model_requests == 2
        await writer.append(thread_id="thread-root", records=(_model(0, run="run-new"),))
        current = await reader.snapshot(thread_id="thread-root")
        assert current.combined.model_requests == 3
        assert current == await ThreadUsageRepository(database.sessions).snapshot(thread_id="thread-root")


async def test_legacy_and_auxiliary_records_reaggregate_without_repricing_or_context_pollution(tmp_path: Path) -> None:
    import json

    from a13n_harness_ui.storage.models import ThreadUsageRecord

    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        legacy = _model(0).model_dump(mode="json")
        for field in (
            "call_id",
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
        assert await repository.latest_root_request(thread_id="thread-root") == ModelUsageRecord.model_validate(legacy)
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


@pytest.mark.parametrize("call_id", [None, "call_dispatch"])
async def test_current_usage_identity_survives_transport_live_and_storage(tmp_path, call_id):
    from a13n_harness_ui.live import LiveEvent, model_usage
    from a13n_stream_protocol import HarnessAguiObserver

    record = _model(0).model_copy(update={"call_id": call_id})
    raw = record.model_dump(mode="json")
    report = _report(record)
    extension = report.event.model_copy(
        update={"schema_version": "1", "payload": {**report.event.payload, "records": [raw]}}
    )
    report = replace(report, event=extension)
    transport = HarnessAguiObserver().observe(report)
    assert len(transport) == 1
    wire = transport[0].model_dump(mode="json")
    assert wire["value"]["event"]["schema_version"] == "1"
    assert wire["value"]["event"]["payload"]["records"] == [raw]
    live = LiveEvent(
        epoch="epoch",
        sequence=1,
        run_kind="root",
        root_thread_id="thread-root",
        thread_id="thread-root",
        run_id="run-root",
        event_type="CUSTOM",
        payload=wire,
        payload_omitted=False,
    )
    assert model_usage(live) == (record,)
    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thread-root"))
        repository = ThreadUsageRepository(database.sessions)
        await repository.observe(thread_id="thread-root", item=report)
        assert await repository.latest_root_request(thread_id="thread-root") == record


async def test_usage_without_call_id_survives_transport_live_and_storage(tmp_path):
    from a13n_harness_ui.live import LiveEvent, model_usage
    from a13n_stream_protocol import HarnessAguiObserver

    record = _model(0).model_copy(update={"call_id": None})
    raw = record.model_dump(mode="json")
    raw.pop("call_id")
    report = _report(record)
    extension = report.event.model_copy(
        update={
            "schema_version": "1",
            "payload": {**report.event.payload, "records": [raw]},
        }
    )
    report = replace(report, event=extension)
    assert HarnessAguiObserver().observe(report)
    live = LiveEvent(
        epoch="epoch",
        sequence=1,
        run_kind="root",
        root_thread_id="thread-root",
        thread_id="thread-root",
        run_id="run-root",
        event_type="CUSTOM",
        payload={"value": {"event": extension.model_dump(mode="json")}},
        payload_omitted=False,
    )
    assert model_usage(live) == (record,)
    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thread-root"))
        repository = ThreadUsageRepository(database.sessions)
        await repository.observe(thread_id="thread-root", item=report)
        assert await repository.latest_root_request(thread_id="thread-root") == record
        assert (await repository.snapshot(thread_id="thread-root")).combined.model_requests == 1


def _snapshot(*records, sequence=1, usage_id="usage-scope", thread_id="thr_root"):
    first = records[0]
    return UsageSnapshot(
        usage_id=usage_id,
        thread_id=thread_id,
        run_id=first.run_id,
        agent_instance_id=first.agent_instance_id,
        parent_agent_instance_id=first.parent_agent_instance_id,
        delegation_id=first.delegation_id,
        sequence=sequence,
        records=records,
    )


async def test_latest_snapshots_replace_in_place_and_are_visible_to_another_engine(tmp_path):
    from a13n_harness import StateError
    from a13n_harness_ui.storage.models import ThreadUsageRecord
    from sqlalchemy import func

    path = tmp_path / "metadata.sqlite3"
    settings = StorageSettings(data_root=tmp_path)
    async with open_database(path, settings) as first_db, open_database(path, settings) as second_db:
        async with transaction(first_db.sessions) as session:
            session.add(_thread("thr_root"))
        writer, reader = ThreadUsageRepository(first_db.sessions), ThreadUsageRepository(second_db.sessions)
        first = _snapshot(_model(0, cost=Decimal("0.1")), _receipt())
        await writer.save(thread_id="thr_root", snapshot=first)
        first_view = await reader.snapshot(thread_id="thr_root")
        assert first_view.combined.model_requests == 1
        updated_record = first.records[0].model_copy(
            update={
                "request_usage": BoundedRequestUsage(input_tokens=300, cost=Decimal("0.3")),
            }
        )
        latest = first.model_copy(update={"sequence": 2, "records": (updated_record, first.records[1])})
        await writer.save(thread_id="thr_root", snapshot=latest)
        await writer.save(thread_id="thr_root", snapshot=first)
        await reader.save(thread_id="thr_root", snapshot=latest)
        latest_view = await reader.snapshot(thread_id="thr_root")
        assert latest_view.first_observed_at == first_view.first_observed_at
        assert latest_view.observed_through > first_view.observed_through
        totals = latest_view.combined
        assert totals.model_requests == 1 and dict(totals.tokens)["input_tokens"] == 300
        assert totals.model_cost_usd == Decimal("0.3")
        assert totals.provider_costs == (("EUR", Decimal("0.2")),)
        assert await reader.latest_root_request(thread_id="thr_root") == updated_record
        with pytest.raises(StateError, match="conflicting"):
            await writer.save(thread_id="thr_root", snapshot=latest.model_copy(update={"tool_calls": 1}))
        async with transaction(first_db.sessions) as session:
            assert await session.scalar(select(func.count()).select_from(ThreadUsageRecord)) == 1


@pytest.mark.parametrize("cancel", [False, True])
async def test_snapshot_preparation_keeps_loop_responsive_and_admitted_write_settles(tmp_path, monkeypatch, cancel):
    import threading

    from a13n_harness_ui.storage import usage as usage_module
    from a13n_harness_ui.storage.push import PushRepository
    from anyio import CancelScope, Event, create_task_group, fail_after, from_thread

    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thr_root"))
        repository = ThreadUsageRepository(database.sessions)
        push = PushRepository(database.sessions)
        key = await push.private_key()
        await repository.save(thread_id="thr_root", snapshot=_snapshot(_model(0)))
        loop_thread = threading.get_ident()
        entered, finished = Event(), Event()
        release = threading.Event()
        original = usage_module._prepare_snapshot_write
        scope = CancelScope()

        def prepare(snapshot, previous_payload):
            # A misplaced inline call fails immediately instead of blocking the test loop.
            assert threading.get_ident() != loop_thread
            assert previous_payload is not None
            from_thread.run_sync(entered.set)
            assert release.wait(5)
            return original(snapshot, previous_payload)

        async def save():
            with scope:
                await repository.save(thread_id="thr_root", snapshot=_snapshot(_model(0), _model(1), sequence=2))
            finished.set()

        monkeypatch.setattr(usage_module, "_prepare_snapshot_write", prepare)
        async with create_task_group() as tasks:
            tasks.start_soon(save)
            try:
                with fail_after(2):
                    await entered.wait()
                    # The write remains atomic, but readers and loop callbacks can proceed.
                    assert database.sessions.write_lock.locked()
                    assert await push.private_key() == key
                    if cancel:
                        scope.cancel()
                    assert not finished.is_set()
            finally:
                release.set()
            with fail_after(2):
                await finished.wait()
        assert (await repository.snapshot(thread_id="thr_root")).combined.model_requests == 2


async def test_snapshots_and_legacy_receipts_share_one_attribution_without_losing_child_usage(tmp_path):
    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thr_root"))
            await session.flush()
            session.add(_thread("thr_child", "thr_root"))
        repository = ThreadUsageRepository(database.sessions)
        await repository.append(thread_id="thr_root", records=(_model(0, run="legacy"), _receipt()))
        await repository.save(thread_id="thr_root", snapshot=_snapshot(_model(0), _receipt()))
        child = _snapshot(_model(0, run="child"), _receipt(run="child"), usage_id="usage-child", thread_id="thr_child")
        await repository.save(thread_id="thr_child", snapshot=child)
        view = await repository.snapshot(thread_id="thr_root")
        assert view.combined.model_requests == 3
        assert view.root.model_requests == 2 and view.descendants.model_requests == 1
        assert view.combined.provider_receipts == 1 and view.root.provider_receipts == 1
        assert view.descendants.provider_receipts == 0
        changed = child.model_copy(
            update={
                "sequence": 2,
                "records": (
                    child.records[0],
                    _receipt(run="child", cost=Decimal("99")),
                ),
            }
        )
        with pytest.raises(Exception, match=r"attribution|receipt"):
            await repository.save(thread_id="thr_child", snapshot=changed)


async def test_restore_uses_latest_accounting_without_changing_execution_history(tmp_path):
    from a13n_harness import HarnessState
    from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
    from a13n_harness.usage import USAGE_CAPABILITY_ID

    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thr_root"))
        repository = ThreadUsageRepository(database.sessions)
        first = _snapshot(_model(0))
        latest = _snapshot(_model(0), _model(1), sequence=2)
        state = HarnessState.new(
            thread_id="thr_root",
            agent_context_state=AgentContextStateSnapshot(
                entries={
                    USAGE_CAPABILITY_ID: CapabilityState(version="1", data=first.model_dump(mode="json")),
                }
            ),
        )
        await repository.save(thread_id="thr_root", snapshot=latest)
        restored = await repository.restore(thread_id="thr_root", state=state)
        assert UsageSnapshot.from_state(restored) == latest
        assert restored.message_history == state.message_history
        assert UsageSnapshot.from_state(state) == first


async def test_shipped_usage_payload_survives_upgrade_and_current_snapshot_updates(tmp_path):
    from a13n_harness_ui.storage.models import ThreadUsageRecord

    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "11422c5bac45"), write=True)
    # Literal pre-snapshot payload: never normalize it through today's models before storage.
    raw = (
        '{"kind":"model","record_id":"old-fact","run_id":"old-run",'
        '"response_ordinal":0,"agent_instance_id":"old-agent","response_state":"complete",'
        '"response_timestamp":"2026-09-07T00:00:00Z",'
        '"request_usage":{"input_tokens":7,"output_tokens":2,"cost":"0.125"}}'
    )
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                Table("thread", MetaData(), autoload_with=connection)
                .insert()
                .values(
                    thread_id="thr_root",
                    title="Retain me",
                    archived=False,
                    metadata_version=1,
                    created_at=_NOW,
                    updated_at=_NOW,
                    initial_state_schema_version="1",
                    initial_state_digest="a" * 64,
                )
            )
            connection.execute(
                Table("thread_usage", MetaData(), autoload_with=connection)
                .insert()
                .values(
                    root_thread_id="thr_root",
                    origin_thread_id="thr_root",
                    record_id="old-fact",
                    run_id="old-run",
                    descendant=False,
                    payload_json=raw,
                    observed_at=_NOW,
                )
            )
    finally:
        engine.dispose()
    for _ in range(2):
        async with open_database(path, StorageSettings(data_root=tmp_path)) as database:
            repository = ThreadUsageRepository(database.sessions)
            await repository.save(thread_id="thr_root", snapshot=_snapshot(_model(0, cost=Decimal("0.2"))))
            view = await repository.snapshot(thread_id="thr_root")
            assert view.combined.model_requests == 2
            assert view.combined.model_cost_usd == Decimal("0.325")
            async with transaction(database.sessions) as session:
                assert (
                    await session.scalar(
                        select(ThreadUsageRecord.payload_json).where(
                            ThreadUsageRecord.record_id == "old-fact",
                        )
                    )
                    == raw
                )
                assert await session.scalar(select(ThreadRecord.title)) == "Retain me"


async def test_projection_pins_one_read_snapshot_during_concurrent_replacement(tmp_path, monkeypatch):
    import asyncio

    path = tmp_path / "metadata.sqlite3"
    settings = StorageSettings(data_root=tmp_path)
    async with open_database(path, settings) as first_db, open_database(path, settings) as second_db:
        async with transaction(first_db.sessions) as session:
            session.add(_thread("thr_root"))
        writer, reader = ThreadUsageRepository(first_db.sessions), ThreadUsageRepository(second_db.sessions)
        await writer.save(thread_id="thr_root", snapshot=_snapshot(_model(0)))
        entered, release = asyncio.Event(), asyncio.Event()
        original = reader._root_id

        async def pause_after_first_read(session, thread_id):
            result = await original(session, thread_id)
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(reader, "_root_id", pause_after_first_read)
        pending = asyncio.create_task(reader.snapshot(thread_id="thr_root"))
        await entered.wait()
        try:
            await writer.save(thread_id="thr_root", snapshot=_snapshot(_model(0), _model(1), sequence=2))
        finally:
            release.set()
        assert (await pending).combined.model_requests == 1
        assert (await reader.snapshot(thread_id="thr_root")).combined.model_requests == 2


async def test_usage_queries_extract_envelope_metadata_once_per_scope(tmp_path):
    import json

    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thr_root"))
            await session.flush()
            session.add(_thread("thr_child", "thr_root"))
        repository = ThreadUsageRepository(database.sessions)
        root = _snapshot(*(_model(index) for index in range(50)))
        child = _snapshot(
            *(_model(index, run="child", child=True) for index in range(50)),
            usage_id="usage-child",
            thread_id="thr_child",
        )
        await repository.save(thread_id="thr_root", snapshot=root)
        await repository.save(thread_id="thr_child", snapshot=child)
        envelope_reads = 0

        def json_extract(payload, path):
            nonlocal envelope_reads
            if path == "$.observed_through":
                envelope_reads += 1
            value = json.loads(payload)
            for key in path.removeprefix("$.").split("."):
                value = value.get(key) if isinstance(value, dict) else None
            return json.dumps(value) if isinstance(value, (dict, list)) else value

        # Count SQL expression evaluations, not elapsed time or SQL spelling.
        # Sequential repository calls reuse this pool connection.
        async with database.engine.connect() as connection:
            await connection.run_sync(
                lambda conn: conn.connection.dbapi_connection.create_function("json_extract", 2, json_extract)
            )
        assert await repository.latest_root_request(thread_id="thr_root") == root.records[-1]
        assert envelope_reads == 1  # Descendant scopes are excluded before flattening.
        envelope_reads = 0
        view = await repository.snapshot(thread_id="thr_root")
        assert view.combined.model_requests == 100
        assert envelope_reads == 4  # Two coherent queries, each reading two envelopes.


async def test_repeated_receipts_do_not_rescan_family_in_write_transaction(tmp_path):
    from sqlalchemy import event

    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thr_root"))
        repository = ThreadUsageRepository(database.sessions)
        await repository.save(thread_id="thr_root", snapshot=_snapshot(_model(0), _receipt()))
        statements = []

        def observe(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(database.engine.sync_engine, "before_cursor_execute", observe)
        await repository.save(thread_id="thr_root", snapshot=_snapshot(_model(0), _model(1), _receipt(), sequence=2))
        assert not any("json_each" in statement for statement in statements)
        statements.clear()
        new_receipt = _receipt().model_copy(update={"record_id": "usage-second-receipt"})
        await repository.save(
            thread_id="thr_root", snapshot=_snapshot(_model(0), _model(1), _receipt(), new_receipt, sequence=3)
        )
        assert sum("json_each" in statement for statement in statements) == 1
        event.remove(database.engine.sync_engine, "before_cursor_execute", observe)
        assert (await repository.snapshot(thread_id="thr_root")).combined.provider_receipts == 2


@pytest.mark.parametrize("failures", [1, 2, 3])
@pytest.mark.parametrize("failure_kind", ["timeout", "busy", "locked"])
async def test_reporter_retries_only_transient_failures_with_warning(
    tmp_path, monkeypatch, caplog, failures, failure_kind
):
    import sqlite3

    from a13n_harness_ui.storage import usage as usage_module
    from sqlalchemy.exc import OperationalError

    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thr_root"))
        repository = ThreadUsageRepository(database.sessions)
        original = repository.save
        attempts, delays = [], []
        snapshot = _snapshot(_model(0))
        error = TimeoutError("storage deadline")
        if failure_kind != "timeout":
            driver_error = sqlite3.OperationalError("database unavailable")
            driver_error.sqlite_errorcode = sqlite3.SQLITE_BUSY if failure_kind == "busy" else sqlite3.SQLITE_LOCKED
            error = OperationalError("", {}, driver_error)

        async def save(*, thread_id, snapshot):
            attempts.append(snapshot)
            if len(attempts) <= failures:
                raise error
            await original(thread_id=thread_id, snapshot=snapshot)

        async def backoff(delay):
            assert not database.sessions.write_lock.locked()
            delays.append(delay)

        monkeypatch.setattr(repository, "save", save)
        monkeypatch.setattr(usage_module, "sleep", backoff)
        if failures == 3:
            with pytest.raises(type(error)):
                await repository.reporter("thr_root").report(snapshot)
        else:
            await repository.reporter("thr_root").report(snapshot)
        assert attempts == [snapshot] * min(failures + 1, 3)
        assert delays == [0.1, 0.2][: min(failures, 2)]
        warnings = [r for r in caplog.records if r.message == "Usage persistence attempt timed out or was busy"]
        assert len(warnings) == failures
        assert [r.attempt for r in warnings] == list(range(1, failures + 1))
        assert all(r.usage_id == snapshot.usage_id and r.usage_sequence == snapshot.sequence for r in warnings)
        assert warnings[-1].retrying is (failures < 3)
        assert (await repository.snapshot(thread_id="thr_root")).combined.model_requests == int(failures < 3)


@pytest.mark.parametrize("failure_kind", ["conflict", "disk", "cancelled"])
async def test_reporter_does_not_retry_permanent_errors_or_cancellation(tmp_path, monkeypatch, failure_kind):
    import asyncio
    import sqlite3

    from sqlalchemy.exc import OperationalError

    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        repository = ThreadUsageRepository(database.sessions)
        attempts = []
        error = StoreIntegrityError("conflict", code="usage_record_conflict")
        if failure_kind == "disk":
            driver_error = sqlite3.OperationalError("disk I/O error")
            driver_error.sqlite_errorcode = sqlite3.SQLITE_IOERR
            error = OperationalError("", {}, driver_error)
        elif failure_kind == "cancelled":
            error = asyncio.CancelledError()

        async def save(**kwargs):
            attempts.append(kwargs)
            raise error

        monkeypatch.setattr(repository, "save", save)
        with pytest.raises(type(error)):
            await repository.reporter("thr_root").report(_snapshot(_model(0)))
        assert len(attempts) == 1


async def test_harness_reporter_waits_past_old_deadline_and_persists_once(tmp_path, caplog):
    import asyncio

    from a13n_harness.metering import ModelUsageBinding

    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thr_root"))
        repository = ThreadUsageRepository(database.sessions)
        ledger = ModelUsageBinding.standalone(source="test").ledger
        ledger.reporter = repository.reporter("thr_root")
        ledger._append(
            _model(0).model_copy(
                update={"run_id": ledger.run_id, "agent_instance_id": ledger.instance.agent_instance_id}
            )
        )
        async with database.sessions.write_lock:
            pending = asyncio.create_task(ledger._flush(reason="model_request"))
            # This regression specifically crosses the removed five-second deadline.
            await asyncio.sleep(5.1)
            assert not pending.done()
        await pending
        await ledger._flush(reason="terminal")
        assert not ledger._pending
        assert (await repository.snapshot(thread_id="thr_root")).combined.model_requests == 1
        assert "Usage persistence completed slowly" in caplog.text


async def test_reporter_retries_commit_with_lost_acknowledgement_without_double_counting(tmp_path, monkeypatch):
    settings = StorageSettings(data_root=tmp_path)
    async with open_database(tmp_path / "metadata.sqlite3", settings) as database:
        async with transaction(database.sessions) as session:
            session.add(_thread("thr_root"))
        repository = ThreadUsageRepository(database.sessions)
        original = repository.save
        attempts = []

        async def save(**kwargs):
            await original(**kwargs)
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise TimeoutError("acknowledgement lost after commit")

        monkeypatch.setattr(repository, "save", save)
        await repository.reporter("thr_root").report(_snapshot(_model(0)))
        assert len(attempts) == 2
        assert (await repository.snapshot(thread_id="thr_root")).combined.model_requests == 1

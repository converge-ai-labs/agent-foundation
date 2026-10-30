from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from a13n_harness.usage import BoundedRequestUsage, UsageSnapshot
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.database import open_database, transaction
from a13n_harness_ui.storage.models import ThreadConfigurationRecord, ThreadUsageRecord
from a13n_harness_ui.storage.usage import ThreadUsageRepository
from a13n_harness_ui.storage.usage_analytics import UsageAnalyticsRepository, UsageQuery, model_key
from pydantic import ValidationError

from .test_thread_usage import _model, _receipt, _thread

pytestmark = pytest.mark.anyio


def _query(**kwargs):
    return UsageQuery(start=date(2026, 9, 7), end=date(2026, 9, 9), **kwargs)


def _configuration(thread_id, project_id, kind="agent"):
    return ThreadConfigurationRecord(
        thread_id=thread_id,
        project_id=project_id,
        version=1,
        agent_source_kind=kind,
        agent_source_id="agent",
        environment_profile_id="local",
        harness_plugin_ids_json="[]",
        environment_run_extension_ids_json="[]",
        mcp_server_ids_json="[]",
    )


async def test_all_retained_families_and_current_project_scope(tmp_path):
    async with open_database(tmp_path / "db.sqlite", StorageSettings(data_root=tmp_path)) as db:
        async with transaction(db.sessions) as session:
            for name in ("root", "archived", "deleted-project", "memory", "global-memory", "live", "worker"):
                row = _thread(name)
                row.archived = name == "archived"
                row.memory_scope = name if "memory" in name else None
                session.add(row)
            await session.flush()
            session.add(_thread("child", "root"))
            for name, project, kind in (
                ("root", "p", "agent"),
                ("archived", "p", "agent"),
                ("deleted-project", "gone", "agent"),
                ("memory", "p", "memory"),
                ("global-memory", None, "memory"),
                ("live", None, "live"),
                ("worker", "p", "agent"),
            ):
                session.add(_configuration(name, project, kind))
        writer = ThreadUsageRepository(db.sessions)
        for name in ("root", "archived", "deleted-project", "memory", "global-memory", "live", "worker"):
            # Identical record IDs in different root families must all survive.
            await writer.append(thread_id=name, records=(_model(0, cost=Decimal("0.00000001")), _receipt()))
        await writer.append(thread_id="child", records=(_model(1, run="child"),))
        reader = UsageAnalyticsRepository(db.sessions)
        view = await reader.snapshot(_query(group_by="thread", limit=2), project_names={"p": "Project"})
        assert view.summary.model_requests == 8
        assert view.summary.total_tokens == 960
        assert Decimal(view.summary.model_cost_usd) == Decimal("0.00000007")
        assert view.summary.unknown_model_costs == 1
        assert view.group_count == 7
        assert len(view.groups) == 2
        assert sum(day.totals.total_tokens for day in view.daily) == view.summary.total_tokens
        assert dict((item.key, item.label) for item in view.projects)["gone"] == "gone (unavailable)"
        assert any(item.label == "Memory · Global" for item in view.threads)
        assert any(item.label == "Live" for item in view.threads)
        project = await reader.snapshot(_query(project_id="p"), project_names={"p": "Project"})
        assert project.summary.model_requests == 5
        projectless = await reader.snapshot(_query(projectless=True), project_names={})
        assert projectless.summary.model_requests == 2
        root = await reader.snapshot(_query(thread_id="root"), project_names={})
        old = await writer.snapshot(thread_id="root")
        assert root.summary.model_requests == old.combined.model_requests
        assert root.summary.input_tokens == dict(old.combined.tokens)["input_tokens"]
        async with transaction(db.sessions) as session:
            config = await session.get(ThreadConfigurationRecord, "root")
            config.project_id = "moved"
        moved = await reader.snapshot(_query(project_id="moved"), project_names={})
        assert moved.summary.model_requests == 2


async def test_latest_snapshot_response_date_and_actual_model_filters(tmp_path):
    async with open_database(tmp_path / "db.sqlite", StorageSettings(data_root=tmp_path)) as db:
        async with transaction(db.sessions) as session:
            session.add(_thread("root"))
        writer = ThreadUsageRepository(db.sessions)
        record = _model(0, cost=Decimal("0"))
        snapshot = UsageSnapshot(
            usage_id="scope",
            thread_id="root",
            run_id=record.run_id,
            agent_instance_id=record.agent_instance_id,
            sequence=1,
            records=(record,),
        )
        await writer.save(thread_id="root", snapshot=snapshot)
        reader = UsageAnalyticsRepository(db.sessions)
        initial = await reader.snapshot(_query(), project_names={})
        assert initial.daily[0].totals.model_cost_usd == "0"
        updated = record.model_copy(
            update={
                "response_timestamp": datetime(2026, 9, 8, 2, tzinfo=timezone(timedelta(hours=2))),
                "request_usage": BoundedRequestUsage(input_tokens=200, output_tokens=40, cost=Decimal("0.5")),
            }
        )
        await writer.save(thread_id="root", snapshot=snapshot.model_copy(update={"sequence": 2, "records": (updated,)}))
        auxiliary = _model(1).model_copy(update={"source": "reviewer", "model_name": "other"})
        await writer.append(thread_id="root", records=(auxiliary,))
        view = await reader.snapshot(_query(model=model_key(updated)), project_names={})
        assert view.summary.model_requests == 1
        assert view.daily[0].totals.model_requests == 0
        assert view.daily[1].totals.total_tokens == 240
        assert len(view.models) == 2  # Contextual choices ignore only the model filter.
        assert view.first_observed_at == initial.first_observed_at
        assert view.observed_through >= initial.observed_through
        boundary = await reader.snapshot(UsageQuery(start=date(2026, 9, 7), end=date(2026, 9, 8)), project_names={})
        assert boundary.summary.model_requests == 1
        assert boundary.summary.model_cost_usd is None


@pytest.mark.parametrize("snapshot_first", [False, True])
async def test_scope_and_legacy_duplicates_are_selected_before_filters(tmp_path, snapshot_first):
    async with open_database(tmp_path / "db.sqlite", StorageSettings(data_root=tmp_path)) as db:
        async with transaction(db.sessions) as session:
            session.add(_thread("root"))
        writer = ThreadUsageRepository(db.sessions)
        winner = _model(0, cost=Decimal("0.000000000123"))
        later = winner.model_copy(
            update={"model_name": "later", "response_timestamp": datetime(2026, 9, 8, tzinfo=UTC)}
        )
        snapshot = UsageSnapshot(
            usage_id="scope",
            thread_id="root",
            run_id=winner.run_id,
            agent_instance_id=winner.agent_instance_id,
            sequence=1,
            records=(winner if snapshot_first else later, _model(1)),
        )
        if snapshot_first:
            await writer.save(thread_id="root", snapshot=snapshot)
            # Seed a retained legacy row directly; current append rejects conflicting facts.
            async with transaction(db.sessions) as session:
                session.add(
                    ThreadUsageRecord(
                        root_thread_id="root",
                        origin_thread_id="root",
                        record_id=later.record_id,
                        run_id=later.run_id,
                        descendant=False,
                        payload_json=later.model_dump_json(),
                        observed_at=datetime.now(UTC),
                    )
                )
        else:
            await writer.append(thread_id="root", records=(winner,))
            await writer.save(thread_id="root", snapshot=snapshot)
        # A second scope repeats both IDs; neither should be counted again.
        await writer.save(thread_id="root", snapshot=snapshot.model_copy(update={"usage_id": "second"}))
        reader = UsageAnalyticsRepository(db.sessions)
        view = await reader.snapshot(_query(), project_names={})
        ledger = await writer.snapshot(thread_id="root")
        assert view.summary.model_requests == ledger.combined.model_requests == 2
        assert Decimal(view.summary.model_cost_usd) == Decimal("0.000000000123")
        assert view.summary.unknown_model_costs == 1
        assert len(view.models) == 1
        filtered = await reader.snapshot(_query(model=model_key(later)), project_names={})
        assert filtered.summary.model_requests == 0
        next_day = await reader.snapshot(UsageQuery(start=date(2026, 9, 8), end=date(2026, 9, 9)), project_names={})
        assert next_day.summary.model_requests == 0


async def test_metadata_and_usage_share_one_read_snapshot(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    async with open_database(tmp_path / "db.sqlite", StorageSettings(data_root=tmp_path)) as db:
        async with transaction(db.sessions) as session:
            session.add(_thread("root"))
            await session.flush()
            session.add(_configuration("root", "before"))
        writer = ThreadUsageRepository(db.sessions)
        record = _model(0)
        snapshot = UsageSnapshot(
            usage_id="scope",
            thread_id="root",
            run_id=record.run_id,
            agent_instance_id=record.agent_instance_id,
            sequence=1,
            records=(record,),
        )
        await writer.save(thread_id="root", snapshot=snapshot)
        original_stream = AsyncSession.stream
        changed = False

        async def concurrent_write(session, *args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                async with transaction(db.sessions) as other:
                    config = await other.get(ThreadConfigurationRecord, "root")
                    config.project_id = "after"
                updated = record.model_copy(update={"request_usage": BoundedRequestUsage(input_tokens=900)})
                await writer.save(
                    thread_id="root", snapshot=snapshot.model_copy(update={"sequence": 2, "records": (updated,)})
                )
            return await original_stream(session, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, "stream", concurrent_write)
        reader = UsageAnalyticsRepository(db.sessions)
        before = await reader.snapshot(_query(project_id="before"), project_names={})
        assert before.summary.total_tokens == 120
        assert before.groups[0].project_id == "before"
        after = await reader.snapshot(_query(project_id="after"), project_names={})
        assert after.summary.total_tokens == 900
        assert after.groups[0].project_id == "after"


async def test_stable_paging_cost_order_and_empty_coverage(tmp_path):
    async with open_database(tmp_path / "db.sqlite", StorageSettings(data_root=tmp_path)) as db:
        async with transaction(db.sessions) as session:
            for name in ("a", "b", "c"):
                session.add(_thread(name))
        writer = ThreadUsageRepository(db.sessions)
        for name, cost in (("a", Decimal("0")), ("b", None), ("c", Decimal("0.1"))):
            await writer.append(thread_id=name, records=(_model(0, cost=cost),))
        reader = UsageAnalyticsRepository(db.sessions)
        pages = [
            await reader.snapshot(_query(group_by="thread", order_by="cost", offset=i, limit=1), project_names={})
            for i in range(3)
        ]
        assert [page.groups[0].key for page in pages] == ["c", "a", "b"]
        assert pages[0].summary == pages[1].summary == pages[2].summary
        empty = await reader.snapshot(_query(thread_id="missing"), project_names={})
        assert empty.summary.model_cost_usd is None
        assert empty.first_observed_at is None
        assert empty.group_count == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"end": date(2026, 9, 7)},
        {"end": date(2027, 9, 9)},
        {"project_id": "p", "projectless": True},
        {"limit": 101},
        {"offset": -1},
    ],
)
def test_invalid_filters(changes):
    with pytest.raises(ValidationError):
        UsageQuery.model_validate({"start": date(2026, 9, 7), "end": date(2026, 9, 9), **changes})


async def test_http_contract_instance_auth_and_invalid_ranges(tmp_path):
    from .test_sharing_webui import create_link, shared_server

    async with shared_server(tmp_path) as (_, app, client):
        thread = await app.create_thread(title="Usage fixture")
        await app._store.usage.append(thread_id=thread.thread_id, records=(_model(0, cost=Decimal("0.125")),))
        params = {"start": "2026-09-07", "end": "2026-09-09", "thread_id": thread.thread_id}
        shell = await client.get("/usage?thread_id=" + thread.thread_id)
        assert shell.status_code == 200
        assert shell.headers["content-type"].startswith("text/html")
        path = "/api/usage"
        assert (await client.get(path, params=params)).status_code == 401
        prefix, guest = await create_link(client, "thread", thread.thread_id)
        assert (await client.get(path, params=params, headers=guest)).status_code == 401
        assert (await client.get(prefix + "/usage", params=params, headers=guest)).status_code == 404
        owner = {"Authorization": "Bearer owner-test-key"}
        response = await client.get(path, params=params, headers=owner)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["summary"]["model_cost_usd"] == "0.125"
        assert payload["summary"]["total_tokens"] == 120
        assert payload["group_count"] == 1
        assert (await client.get(path, params={**params, "end": "2028-01-01"}, headers=owner)).status_code == 422
        assert (await client.get(path, params={**params, "limit": 101}, headers=owner)).status_code == 422

"""Submission evidence, retries, authorization and overlap use real PostgreSQL state."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, rate_limited
from a13n_service.runs.schedules import dispatch, queries
from a13n_service.runs.schedules.tables import OccurrenceRow, ScheduleRow
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.tenancy.tables import PrincipalRow
from sqlalchemy import event, func, select

pytestmark = pytest.mark.anyio


async def schedule_for(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    response = await service.client.post(
        f"{service.api}/schedules",
        json={
            "name": "Manual report",
            "schedule": {"expression": "0 9 * * *", "timezone": "UTC"},
            "invocation": {
                "agent_id": agent["id"],
                "agent_revision_id": None,
                "payload": {"content": [{"type": "text", "text": "Report today"}]},
            },
        },
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    return response.json(), agent


async def trigger(service, schedule, runs_kit, key="manual"):  # type: ignore[no-untyped-def]
    response = await service.client.post(
        f"{service.api}/schedules/{schedule['id']}/dispatch",
        json={},
        headers={**runs_kit.if_match(schedule), "idempotency-key": key},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def observed_execution(service, schedule, occurrence_id):  # type: ignore[no-untyped-def]
    response = await service.client.get(
        f"{service.api}/schedules/{schedule['id']}/occurrences/{occurrence_id}", params={"include": "execution"}
    )
    assert response.status_code == 200, response.text
    return response.json()["execution"]


async def test_manual_replay_concurrent_processing_and_run_origin(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    item = await trigger(service, schedule, runs_kit)
    assert item["status"] == "pending" and item["thread_id"] is None
    assert item["dispatch_attempts"] == 0
    assert "execution" not in item
    pending = await observed_execution(service, schedule, item["id"])
    assert pending["initial_entry_status"] is None and pending["initial_run"] is None
    assert not pending["has_unfinished_work"]
    await asyncio.gather(*(dispatch.process(service.runtime, schedule["id"]) for _ in range(3)))
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.status == "submitted" and stored.next_attempt_at is None
        entry = await session.get_one(InboxEntryRow, stored.entry_id)
        assert entry.status == "assigned"
        thread = await session.get_one(ThreadRow, stored.thread_id)
        assert thread.schedule_occurrence_id == item["id"]
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 1
        assert await session.scalar(select(func.count()).select_from(InboxEntryRow)) == 1
        run_id = entry.assigned_run_id
    run = (await service.client.get(f"{service.api}/runs/{run_id}")).json()
    assert run["trigger"] == "input" and run["lineage"] == "root"
    assert run["schedule_origin"] == {"schedule_id": schedule["id"], "occurrence_id": item["id"]}
    replay = await service.client.post(
        f"{service.api}/schedules/{schedule['id']}/dispatch", json={}, headers={"idempotency-key": "manual"}
    )
    assert replay.status_code == 200 and replay.json()["id"] == item["id"]
    assert replay.json()["status"] == "submitted"
    busy = await service.client.post(
        f"{service.api}/schedules/{schedule['id']}/dispatch",
        json={},
        headers={**runs_kit.if_match(schedule), **runs_kit.fresh_key()},
    )
    assert busy.status_code == 409 and busy.json()["error"]["details"]["reason"] == "schedule_busy"
    page = (
        await service.client.get(
            f"{service.api}/threads", params={"session_id": schedule["session_id"], "occurrence_id": item["id"]}
        )
    ).json()
    assert [t["id"] for t in page["items"]] == [thread.id]


async def test_submitted_without_run_is_not_reposted_or_withdrawn(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    item = await trigger(service, schedule, runs_kit)
    monkeypatch.setattr(dispatch, "accept", AsyncMock(return_value=None))
    await dispatch.process(service.runtime, schedule["id"])
    execution = await observed_execution(service, schedule, item["id"])
    assert execution["initial_entry_status"] == "pending" and execution["initial_run"] is None
    assert execution["has_unfinished_work"]
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.status == "submitted" and stored.thread_id and stored.entry_id
        assert (await session.get_one(InboxEntryRow, stored.entry_id)).status == "pending"
        assert await queries.has_unfinished(session, await session.get_one(ScheduleRow, schedule["id"]))
        assert await session.scalar(select(func.count()).select_from(RunRow)) == 0
    paused = await service.client.post(
        f"{service.api}/schedules/{schedule['id']}/pause", json={}, headers=runs_kit.if_match(schedule)
    )
    assert paused.status_code == 200
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        assert (await session.get_one(InboxEntryRow, stored.entry_id)).status == "pending"
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 1


async def test_entry_rejected_by_accept_stays_submitted(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    item = await trigger(service, schedule, runs_kit)
    admission = AsyncMock()
    admission.accept.side_effect = ServiceError("forbidden", "Admission refused")
    await dispatch.process(replace(service.runtime, admission=admission), schedule["id"])
    execution = await observed_execution(service, schedule, item["id"])
    assert execution["initial_entry_status"] == "failed" and execution["initial_run"] is None
    assert not execution["has_unfinished_work"]
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.status == "submitted"
        entry = await session.get_one(InboxEntryRow, stored.entry_id)
        assert entry.status == "failed" and entry.assigned_run_id is None
        row = await session.get_one(ScheduleRow, schedule["id"])
        assert row.pause_reason == "manual" and not await queries.has_unfinished(session, row)
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(InboxEntryRow)) == 1


async def test_retry_rolls_back_candidates_and_honors_backoff(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    item = await trigger(service, schedule, runs_kit)
    admission = AsyncMock()
    admission.accept.side_effect = ServiceError("unavailable", "Private input must not be retained")
    runtime = replace(service.runtime, admission=admission)
    await dispatch.process(runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.status == "pending" and stored.dispatch_attempts == 1 and stored.last_error == "unavailable"
        assert stored.next_attempt_at > await now(session)
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0
        assert await session.scalar(select(func.count()).select_from(InboxEntryRow)) == 0
    await dispatch.process(runtime, schedule["id"])
    assert admission.accept.await_count == 1
    async with transaction(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        stored.next_attempt_at = await now(session)
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.status == "submitted" and stored.dispatch_attempts == 2
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 1


async def test_pause_cancels_pending_and_replay_survives_archive(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    item = await trigger(service, schedule, runs_kit)
    url = f"{service.api}/schedules/{schedule['id']}"
    paused = await service.client.post(f"{url}/pause", json={}, headers=runs_kit.if_match(schedule))
    assert paused.status_code == 200
    await dispatch.process(service.runtime, schedule["id"])
    execution = await observed_execution(service, schedule, item["id"])
    assert execution["initial_entry_status"] is None and execution["initial_run"] is None
    assert not execution["has_unfinished_work"]
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.status == "cancelled" and stored.reason == "schedule_paused"
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0
    assert (await service.client.delete(url, headers=runs_kit.if_match(paused.json()))).status_code == 204
    replay = await service.client.post(f"{url}/dispatch", json={}, headers={"idempotency-key": "manual"})
    assert replay.status_code == 200 and replay.json()["id"] == item["id"]


async def test_current_principal_revocation_auto_pauses_before_input(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    item = await trigger(service, schedule, runs_kit)
    async with transaction(service.runtime.storage) as session:
        principal = await session.get_one(PrincipalRow, schedule["principal_id"])
        principal.status = "disabled"
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        row = await session.get_one(ScheduleRow, schedule["id"])
        assert stored.status == "failed" and stored.reason == row.pause_reason == "authority_invalid"
        assert row.state == "paused" and row.pending_occurrence_id is None
        assert await session.scalar(select(func.count()).select_from(ThreadRow)) == 0


async def test_retry_after_and_attempt_limit(service, scripted_model, runs_kit, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    item = await trigger(service, schedule, runs_kit)
    monkeypatch.setattr(dispatch, "prepare_thread", AsyncMock(side_effect=rate_limited("Busy", 60)))
    settings = service.runtime.settings.model_copy(
        update={"schedules": service.runtime.settings.schedules.model_copy(update={"dispatch_max_attempts": 2})}
    )
    runtime = replace(service.runtime, settings=settings)
    await dispatch.process(runtime, schedule["id"])
    async with transaction(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.next_attempt_at > await now(session) + timedelta(seconds=50)
        stored.next_attempt_at = await now(session)
    await dispatch.process(runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        assert stored.status == "failed" and stored.reason == "dispatch_attempts_exhausted"
        assert stored.next_attempt_at is None


@pytest.mark.parametrize("failed", [False, True])
async def test_execution_summary_changes_without_changing_submission_version(
    service, scripted_model, runs_kit, failed
) -> None:  # type: ignore[no-untyped-def]
    schedule, agent = await schedule_for(service, scripted_model, runs_kit)
    item = await trigger(service, schedule, runs_kit)
    await dispatch.process(service.runtime, schedule["id"])
    url = f"{service.api}/schedules/{schedule['id']}/occurrences/{item['id']}"
    receipt = (await service.client.get(url)).json()
    assert receipt["status"] == "submitted" and "execution" not in receipt
    before = await observed_execution(service, schedule, item["id"])
    assert before["initial_entry_status"] == "assigned"
    assert before["initial_run"]["status"] == "accepted" and before["has_unfinished_work"]
    if failed:
        admission = AsyncMock()
        admission.proceed.side_effect = ServiceError("rate_limited", "Test execution budget exhausted")
        await (await runs_kit.attempt(service, runtime=replace(service.runtime, admission=admission)))
    else:
        scripted_model.say("Report ready")
        await (await runs_kit.attempt(service))
    after = (await service.client.get(url, params={"include": "execution"})).json()
    execution = after.pop("execution")
    assert after == receipt
    assert execution["initial_run"] == {
        "id": before["initial_run"]["id"],
        "status": "failed" if failed else "completed",
    }
    assert not execution["has_unfinished_work"]
    assert execution["observed_at"] > before["observed_at"]
    if not failed:
        assert execution["initial_entry_status"] == "consumed"
        # A later input starts another Run. The summary keeps the original Run and reports renewed work.
        continued = await runs_kit.submit(service, receipt["thread_id"], runs_kit.message(agent, "Follow up"))
        assert continued.status_code == 201, continued.text
        current = await observed_execution(service, schedule, item["id"])
        assert current["initial_run"] == execution["initial_run"] and current["has_unfinished_work"]


async def test_execution_expansion_observes_one_snapshot_during_submission(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    await trigger(service, schedule, runs_kit)
    selected, release = asyncio.Event(), asyncio.Event()
    read = queries.execution_summaries

    async def between_reads(*args, **kwargs):  # type: ignore[no-untyped-def]
        selected.set()
        await release.wait()
        return await read(*args, **kwargs)

    monkeypatch.setattr(queries, "execution_summaries", between_reads)
    url = f"{service.api}/schedules/{schedule['id']}/occurrences"
    request = asyncio.create_task(service.client.get(url, params={"include": "execution"}))
    try:
        async with asyncio.timeout(10):
            await selected.wait()
            await dispatch.process(service.runtime, schedule["id"])
    finally:
        release.set()
        response = await request
    assert response.status_code == 200, response.text
    observed = response.json()["items"][0]
    assert observed["status"] == "pending"
    assert observed["execution"]["initial_entry_status"] is None
    assert observed["execution"]["initial_run"] is None
    current = (await service.client.get(url, params={"include": "execution"})).json()["items"][0]
    assert current["status"] == "submitted" and current["execution"]["initial_run"]["status"] == "accepted"


async def test_execution_history_batches_reads_and_preserves_pagination_and_scope(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    admission = AsyncMock()
    admission.accept.side_effect = ServiceError("forbidden", "Test refusal before Run creation")
    for index in range(5):
        await trigger(service, schedule, runs_kit, key=f"history-{index}")
        await dispatch.process(replace(service.runtime, admission=admission), schedule["id"])
    url = f"{service.api}/schedules/{schedule['id']}/occurrences"
    engine = service.runtime.storage.engine.sync_engine
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        if statement.startswith("SELECT"):
            statements.append(statement)

    async def counted(**params):  # type: ignore[no-untyped-def]
        statements.clear()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = await service.client.get(url, params=params)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert response.status_code == 200, response.text
        return response.json(), list(statements)

    plain, plain_sql = await counted(limit=5)
    one, one_sql = await counted(limit=1, include="execution")
    many, many_sql = await counted(limit=5, include="execution")
    assert len(one_sql) == len(many_sql) == len(plain_sql) + 1
    assert len(many["items"]) == 5
    assert all("execution" not in item for item in plain["items"])
    for item in many["items"]:
        assert item["status"] == "submitted" and "snapshot" not in item
        execution = item["execution"]
        assert execution["initial_entry_status"] == "failed" and execution["initial_run"] is None
        assert not execution["has_unfinished_work"]
    # Projection does not change page membership or invalidate a cursor.
    rest, _ = await counted(limit=5, cursor=one["next_cursor"])
    assert [one["items"][0]["id"], *[item["id"] for item in rest["items"]]] == [item["id"] for item in plain["items"]]
    assert (await service.client.get(url, params={"include": "unknown"})).status_code == 400
    item_url = f"{url}/{one['items'][0]['id']}"
    assert (await service.client.get(item_url, params={"include": "unknown"})).status_code == 400
    other = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other workspace"})
    assert other.status_code == 201, other.text
    for path in (url, item_url):
        response = await service.client.get(
            path, params={"include": "execution"}, headers={"x-workspace-id": other.json()["id"]}
        )
        assert response.status_code == 404


async def test_children_outbox_waiting_and_forks_have_precise_ownership(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.infra.outbox import OutboxRow

    await runs_kit.pause_sweeps(service)
    coordinator = await runs_kit.delegating(service, scripted_model, "async")
    created = await service.client.post(
        f"{service.api}/schedules",
        json={
            "name": "Delegation",
            "schedule": {"expression": "0 9 * * *", "timezone": "UTC"},
            "invocation": {
                "agent_id": coordinator["id"],
                "agent_revision_id": None,
                "payload": {"content": [{"type": "text", "text": "delegate"}]},
            },
        },
        headers=runs_kit.fresh_key(),
    )
    schedule = created.json()
    item = await trigger(service, schedule, runs_kit)
    subscription = await service.client.post(
        f"{service.api}/subscriptions",
        json={
            "name": "Origins",
            "url": "http://127.0.0.1:9/hook",
            "kinds": ["run.accepted", "run.running"],
        },
    )
    assert subscription.status_code == 201
    await dispatch.process(service.runtime, schedule["id"])
    scripted_model.call(
        "delegate", {"subagent_name": "helper", "prompt": "compute"}, call_id="call_d", to="Role: coordinator"
    )
    scripted_model.say("Delegated", to="Role: coordinator")
    await (await runs_kit.attempt(service))
    assert (await observed_execution(service, schedule, item["id"]))["has_unfinished_work"]
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        stored = await session.get_one(OccurrenceRow, item["id"])
        root = await session.get_one(ThreadRow, stored.thread_id)
        child = (await session.scalars(select(ThreadRow).where(ThreadRow.origin_thread_id == root.id))).one()
        assert child.schedule_occurrence_id == item["id"]
        assert await queries.has_unfinished(session, row)
        origin_run_id = root.last_run_id
    scripted_model.say("42", to="Role: worker")
    await (await runs_kit.attempt(service))
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"])
        delivery = (await session.scalars(select(OutboxRow).where(OutboxRow.kind == "child_result"))).one()
        delivery.available_at = await now(session) + timedelta(hours=1)
        # A sealed parent and child still block while their result waits in the outbox.
        assert await queries.has_unfinished(session, row)
        webhooks = (await session.scalars(select(OutboxRow).where(OutboxRow.kind == "webhook"))).all()
        assert {hook.payload["type"] for hook in webhooks} == {"run.accepted", "run.running"}
        assert all(
            hook.payload["run"]["schedule_origin"] == {"schedule_id": schedule["id"], "occurrence_id": item["id"]}
            for hook in webhooks
        )
    # Forks have independent ownership even when they share the fixed Session.
    forked = await service.client.post(
        f"{service.api}/runs/{origin_run_id}/fork",
        json=runs_kit.message(coordinator, "independent"),
        headers=runs_kit.fresh_key(),
    )
    assert forked.status_code == 201, forked.text
    assert forked.json()["thread"]["schedule_occurrence_id"] is None
    assert forked.json()["run"]["schedule_origin"] is None
    # Archive the owned parent: its delayed result no longer blocks, and the independent fork cannot block.
    archived = await service.client.post(
        f"{service.api}/threads/{root.id}/archive",
        headers=runs_kit.if_match(await runs_kit.get_thread(service, root.id)),
    )
    assert archived.status_code == 200, archived.text
    assert not (await observed_execution(service, schedule, item["id"]))["has_unfinished_work"]
    async with short_session(service.runtime.storage) as session:
        assert not await queries.has_unfinished(session, await session.get_one(ScheduleRow, schedule["id"]))


async def test_waiting_and_old_thread_reactivation_block_next_submission(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model, user_questions=True)
    response = await service.client.post(
        f"{service.api}/schedules",
        json={
            "name": "Question",
            "schedule": {"expression": "0 9 * * *", "timezone": "UTC"},
            "invocation": {
                "agent_id": agent["id"],
                "agent_revision_id": None,
                "payload": {"content": [{"type": "text", "text": "Ask"}]},
            },
        },
        headers=runs_kit.fresh_key(),
    )
    schedule = response.json()
    item = await trigger(service, schedule, runs_kit)
    await dispatch.process(service.runtime, schedule["id"])
    scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="call_ask")
    await (await runs_kit.attempt(service))
    waiting = await observed_execution(service, schedule, item["id"])
    assert waiting["initial_run"]["status"] == "waiting" and waiting["has_unfinished_work"]
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, item["id"])
        root = await session.get_one(ThreadRow, stored.thread_id)
        run = await session.get_one(RunRow, root.last_run_id)
        assert run.status == "waiting" and root.current_run_id is None
        assert await queries.has_unfinished(session, await session.get_one(ScheduleRow, schedule["id"]))
    resumed = await service.client.post(
        f"{service.api}/runs/{run.id}/resume",
        json={"approvals": {}, "calls": {"call_ask": {"status": "returned", "value": {"response": "blue"}}}},
        headers=runs_kit.fresh_key(),
    )
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["schedule_origin"]["occurrence_id"] == item["id"]
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    next_item = await trigger(service, schedule, runs_kit, key="next")
    # A manual continuation races between intent creation and its submission. B must recheck all owned Threads.
    continued = await runs_kit.submit(service, root.id, runs_kit.message(agent, "Continue"))
    assert continued.status_code == 201, continued.text
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, next_item["id"])
        assert stored.status == "skipped" and stored.reason == "overlap" and stored.thread_id is None


async def test_dispatch_narrows_authority_and_keys_cannot_cross_schedules(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.tenancy.authorize import Grant, Principal

    first, _ = await schedule_for(service, scripted_model, runs_kit)
    second = (
        await service.client.post(
            f"{service.api}/schedules",
            json={
                "name": "Second",
                "schedule": first["schedule"],
                "invocation": first["invocation"],
            },
            headers=runs_kit.fresh_key(),
        )
    ).json()
    tenant = service.tenant
    actor = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, frozenset({"run"})),)
    )
    accepted, created = await dispatch.manual(
        service.runtime,
        actor,
        tenant.workspace_id,
        first["id"],
        request_key="narrow",
        if_match=f'"{first["id"]}:{first["version"]}"',
    )
    assert created
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, accepted.id)
        assert stored.authority["verbs"] == ["run"]
    with pytest.raises(ServiceError) as error:
        await dispatch.manual(
            service.runtime, actor, tenant.workspace_id, second["id"], request_key="narrow", if_match=None
        )
    assert error.value.details["reason"] == "idempotency_key_reused"
    await dispatch.process(service.runtime, first["id"])
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, accepted.id)
        entry = await session.get_one(InboxEntryRow, stored.entry_id)
        assert entry.authority["verbs"] == ["run"]


async def test_expired_intent_is_finished_even_during_backoff(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.runs.schedules import lifecycle

    schedule, _ = await schedule_for(service, scripted_model, runs_kit)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ScheduleRow, schedule["id"], with_for_update=True)
        current = await now(session)
        item = await lifecycle.occurrence(
            session,
            row,
            current - timedelta(hours=1),
            trigger="manual",
            requested_by_id=row.principal_id,
            request_key="expired",
            request_digest="expired",
        )
        item.next_attempt_at = current + timedelta(hours=1)
        occurrence_id = item.id
    await dispatch.process(service.runtime, schedule["id"])
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(OccurrenceRow, occurrence_id)
        assert stored.status == "skipped" and stored.reason == "missed_deadline" and stored.dispatch_attempts == 0
        assert stored.next_attempt_at is None

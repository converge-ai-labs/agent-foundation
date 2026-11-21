"""Renewable browser control uses PostgreSQL authority across independent runtime pools."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.capabilities.browser import BrowserHumanControl
from a13n_service.infra import outbox
from a13n_service.infra.db import Storage, now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.browser.definition import ViewerGrant
from a13n_service.runs.browsers import admission, control, maintenance
from a13n_service.runs.browsers.delivery import handbacks
from a13n_service.runs.browsers.schemas import RenewControl, Viewer
from a13n_service.runs.browsers.tables import BrowserBlockedRow, BrowserRow
from a13n_service.tenancy.access import principal_for
from sqlalchemy import select, text

from .test_browser_regressions import wait_for_blocked_transaction
from .test_browsers import bound_run, command, setup

pytestmark = pytest.mark.anyio


@pytest.fixture
async def replica(service):
    # Independent engine, pool, sessions, Runtime and process-local caches; only durable backing is shared.
    storage = Storage(service.runtime.settings.database.url.get_secret_value())
    try:
        yield lambda: replace(service.runtime, storage=storage)
    finally:
        await storage.close()


async def renew(runtime, browser, *, generation=None, episode=None, seconds=300):
    async with transaction(runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        actor = await principal_for(session, runtime.access, row.controller_id)
    return await control.renew(
        runtime,
        actor,
        browser["workspace_id"],
        browser["id"],
        RenewControl(
            generation=generation if generation is not None else row.generation,
            episode=episode if episode is not None else row.episode,
            lease_seconds=seconds,
        ),
    )


async def expire_claim(runtime, browser):
    async with transaction(runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"], with_for_update=True)
        row.claim_until = await now(session) - timedelta(seconds=1)


async def read(runtime, browser):
    async with transaction(runtime.storage) as session:
        return await session.get_one(BrowserRow, browser["id"])


async def test_renew_http_is_monotonic_cycle_scoped_and_database_only(service, runs_kit, scripted_model, monkeypatch):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    taken = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id, lease_seconds=60)
    before = await read(service.runtime, browser)

    async def unexpected(*args, **kwargs):
        pytest.fail("application keepalives must not perform supplier I/O")

    monkeypatch.setattr(supplier, "ensure_viewer", unexpected)
    path = f"{service.api}/browsers/{browser['id']}/renew"
    body = {"generation": taken["generation"], "episode": taken["episode"], "lease_seconds": 600}
    response = await service.client.post(path, json=body)  # No If-Match or Idempotency-Key.
    assert response.status_code == 200, response.text
    extended = datetime.fromisoformat(response.json()["control_expires_at"])
    assert extended > before.control_expires_at + timedelta(seconds=530)
    response = await service.client.post(path, json={**body, "lease_seconds": 60})
    assert response.status_code == 200 and datetime.fromisoformat(response.json()["control_expires_at"]) == extended
    assert (await read(service.runtime, browser)).viewer == before.viewer
    for field in ("generation", "episode"):
        response = await service.client.post(path, json={**body, field: body[field] + 1})
        assert response.status_code == 409 and "browser_control_changed" in response.text
    for seconds in (59, 3601):
        response = await service.client.post(path, json={**body, "lease_seconds": seconds})
        assert response.status_code == 400
    viewer = await service.client.post(f"{service.api}/browsers/{browser['id']}/viewer")
    assert viewer.status_code == 200
    assert datetime.fromisoformat(viewer.json()["expires_at"]) == before.control_expires_at


async def test_expired_renew_and_viewer_rejected_before_any_sweep(service, runs_kit, scripted_model, replica):
    _, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.control_expires_at = await now(session) - timedelta(seconds=1)
    with pytest.raises(ServiceError, match="browser control expired"):
        await renew(replica(), browser)
    assert (await read(service.runtime, browser)).control == "human"
    response = await service.client.post(f"{service.api}/browsers/{browser['id']}/viewer")
    assert response.status_code == 409
    await control.reconcile(replica(), browser["workspace_id"], browser["id"])
    assert (await read(service.runtime, browser)).control == "automation"


async def test_renew_waiting_for_other_replica_lock_observes_current_intent(service, runs_kit, scripted_model, replica):
    _, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    other = replica()
    async with transaction(other.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        actor = await principal_for(session, other.access, row.controller_id)
        body = RenewControl(generation=row.generation, episode=row.episode)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"], with_for_update=True)
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        pending = asyncio.create_task(control.renew(other, actor, browser["workspace_id"], browser["id"], body))
        await wait_for_blocked_transaction(service.runtime.storage, pid)
        await control.return_control(session, row, await now(session))
    with pytest.raises(ServiceError, match="browser control changed"):
        await pending
    await control.reconcile(other, browser["workspace_id"], browser["id"])
    assert (await read(other, browser)).control == "automation"


async def test_renew_wins_before_expiry_and_two_sweeps_share_one_ensure(
    service, runs_kit, scripted_model, replica, monkeypatch
):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    other = replica()
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.control_expires_at = await now(session) + timedelta(seconds=5)
        grant = Viewer.model_validate(control.lifecycle.reveal(service.runtime, row, "viewer", row.viewer))
        grant.expires_at = row.control_expires_at
        row.viewer = control.lifecycle.protect(service.runtime, row, "viewer", grant.model_dump(mode="json"))
    renewed = await renew(other, browser, seconds=600)
    entered, finish = asyncio.Event(), asyncio.Event()
    original, calls = supplier.ensure_viewer, []

    async def ensure(*args, **kwargs):
        calls.append(kwargs["expires_at"])
        entered.set()
        await finish.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(supplier, "ensure_viewer", ensure)
    dispatcher = maintenance.ControlMaintenance(service.runtime)
    await dispatcher()
    try:
        await asyncio.wait_for(entered.wait(), 5)
        other_dispatcher = maintenance.ControlMaintenance(other)
        await other_dispatcher()
        await asyncio.gather(*other_dispatcher.inflight)
        assert calls == [renewed.control_expires_at]
    finally:
        finish.set()
        await asyncio.gather(*dispatcher.inflight)
    row = await read(other, browser)
    assert row.control == "human" and row.claim_id is None
    grant = Viewer.model_validate(control.lifecycle.reveal(other, row, "viewer", row.viewer))
    assert grant.expires_at == renewed.control_expires_at


async def test_late_ensure_cannot_reopen_after_another_replica_revokes(
    service, runs_kit, scripted_model, replica, monkeypatch
):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    entered, finish = asyncio.Event(), asyncio.Event()
    original = supplier.ensure_viewer

    async def delayed(*args, **kwargs):
        entered.set()
        await finish.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(supplier, "ensure_viewer", delayed)
    task = asyncio.create_task(command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        before = await read(replica(), browser)
        assert before.human_baseline is not None
        await expire_claim(replica(), browser)
        returned = await command(service, runs_kit, browser["id"], "handback")
        assert returned["control"] == "automation" and supplier.revoked and not supplier.grants
    finally:
        finish.set()
        await task
    current = await read(replica(), browser)
    assert current.control == "automation" and current.viewer is None and not supplier.grants


async def test_replica_crash_recovers_claim_or_uncommitted_revoke(
    service, runs_kit, scripted_model, replica, monkeypatch
):
    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, lease, binding)
    entered = asyncio.Event()
    original_revoke = supplier.revoke_viewer

    async def crash_revoke(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(supplier, "revoke_viewer", crash_revoke)
    task = asyncio.create_task(command(service, runs_kit, browser["id"], "handback"))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await read(replica(), browser)
    assert row.control == "returning" and row.claim_id is not None and supplier.grants
    assert not await outbox.claim(
        service.runtime.storage, "browser_handback", owner="before", limit=10, lease_seconds=60, max_attempts=12
    )
    monkeypatch.setattr(supplier, "revoke_viewer", original_revoke)
    await expire_claim(replica(), browser)
    await control.reconcile(replica(), browser["workspace_id"], browser["id"])
    assert (await read(replica(), browser)).control == "automation" and not supplier.grants
    notices = await outbox.claim(
        service.runtime.storage, "browser_handback", owner="after", limit=10, lease_seconds=60, max_attempts=12
    )
    assert len(notices) == 1


async def test_long_control_registration_starts_notice_ttl_only_after_verified_return(
    service, runs_kit, scripted_model, replica
):
    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, lease, binding)
    async with transaction(service.runtime.storage) as session:
        registration = (await session.scalars(select(BrowserBlockedRow))).one()
        registration.created_at = await now(session) - timedelta(days=3)
        assert registration.expires_at is None
    await renew(replica(), browser, seconds=600)
    supplier.fail_revoke = True
    assert (await command(service, runs_kit, browser["id"], "handback"))["control"] == "returning"
    async with transaction(service.runtime.storage) as session:
        registration = (await session.scalars(select(BrowserBlockedRow))).one()
        assert registration.expires_at is None and not registration.suppressed
    supplier.fail_revoke = False
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.control_due_at = await now(session)
    await control.reconcile(replica(), browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        registration = (await session.scalars(select(BrowserBlockedRow))).one()
        assert registration.expires_at > await now(session) + timedelta(hours=23)
    (notice,) = await outbox.claim(
        service.runtime.storage, "browser_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
    )
    await handbacks(replica())(notice)
    await handbacks(service.runtime)(notice)
    entries = [entry for entry in await runs_kit.inbox(service, lease.thread_id) if entry["kind"] == "browser_handback"]
    assert len(entries) == 1


async def test_unmaintainable_viewer_safely_returns(service, runs_kit, scripted_model, replica, monkeypatch):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)

    async def short(*args, **kwargs):
        supplier.grants.add(args[1])
        return ViewerGrant("https://viewer.example/short", {}, datetime.now(UTC) + timedelta(seconds=5))

    monkeypatch.setattr(supplier, "ensure_viewer", short)
    taken = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    assert taken["control"] == "returning" and taken["failure"] == "browser_viewer_lifetime_short"
    await control.reconcile(replica(), browser["workspace_id"], browser["id"])
    assert not supplier.grants and (await read(replica(), browser)).control == "automation"


async def test_out_of_order_ensure_uses_current_deadline_and_cannot_replace_newer_result(
    service, runs_kit, scripted_model, replica, monkeypatch
):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    entered, finish = asyncio.Event(), asyncio.Event()
    original = supplier.ensure_viewer
    calls = 0

    async def ensure(state, grant_id, *, episode, expires_at):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await finish.wait()
            return ViewerGrant("https://viewer.example/old", {}, expires_at)
        result = await original(state, grant_id, episode=episode, expires_at=expires_at)
        return ViewerGrant("https://viewer.example/current", result.state, result.expires_at)

    monkeypatch.setattr(supplier, "ensure_viewer", ensure)
    taking = asyncio.create_task(command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        other = replica()
        renewed = await renew(other, browser, seconds=600)
        await expire_claim(other, browser)
        await control.reconcile(other, browser["workspace_id"], browser["id"])
    finally:
        finish.set()
        await taking
    row = await read(replica(), browser)
    value = Viewer.model_validate(control.lifecycle.reveal(service.runtime, row, "viewer", row.viewer))
    assert row.control == "human" and row.failure is None
    assert value.expires_at == renewed.control_expires_at
    assert value.url == "https://viewer.example/current"


async def test_viewer_expiry_rejects_renew_and_sensitive_read(service, runs_kit, scripted_model, replica):
    _, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    taken = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        value = Viewer.model_validate(control.lifecycle.reveal(service.runtime, row, "viewer", row.viewer))
        value.expires_at = await now(session) - timedelta(seconds=1)
        row.viewer = control.lifecycle.protect(service.runtime, row, "viewer", value.model_dump(mode="json"))
    response = await service.client.post(
        f"{service.api}/browsers/{browser['id']}/renew",
        json={"generation": taken["generation"], "episode": taken["episode"]},
    )
    assert response.status_code == 409 and "browser_viewer_expired" in response.text
    response = await service.client.post(f"{service.api}/browsers/{browser['id']}/viewer")
    assert response.status_code == 409
    await control.reconcile(replica(), browser["workspace_id"], browser["id"])
    assert (await read(replica(), browser)).control == "automation"


async def test_renew_holding_lock_wins_against_expiry_sweep(service, runs_kit, scripted_model, replica, monkeypatch):
    _, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    entered, finish = asyncio.Event(), asyncio.Event()
    original_now = control.now
    blocker = None

    async def held_now(session):
        nonlocal blocker
        if asyncio.current_task().get_name() == "renew-winner" and not entered.is_set():
            blocker = await session.scalar(text("SELECT pg_backend_pid()"))
            entered.set()
            await finish.wait()
        return await original_now(session)

    monkeypatch.setattr(control, "now", held_now)
    renewing = asyncio.create_task(renew(replica(), browser, seconds=600), name="renew-winner")
    await asyncio.wait_for(entered.wait(), 5)
    sweeping = asyncio.create_task(control.reconcile(service.runtime, browser["workspace_id"], browser["id"]))
    try:
        await wait_for_blocked_transaction(service.runtime.storage, blocker)
    finally:
        finish.set()
    extended, _ = await asyncio.wait_for(asyncio.gather(renewing, sweeping), 10)
    current = await read(replica(), browser)
    assert current.control == "human" and current.control_expires_at == extended.control_expires_at


async def test_only_current_controller_can_renew(service, runs_kit, scripted_model, replica):
    _, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    other = replica()
    async with transaction(other.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        actor = await principal_for(session, other.access, row.controller_id)
        body = RenewControl(generation=row.generation, episode=row.episode)
    # Workspace/provider grants alone cannot renew somebody else's active control episode.
    with pytest.raises(ServiceError, match="browser control changed"):
        await control.renew(
            other, replace(actor, id="usr_other_controller"), browser["workspace_id"], browser["id"], body
        )
    assert (await read(other, browser)).control_expires_at == row.control_expires_at


async def test_renew_during_inflight_ensure_requeues_after_other_lane_observes_claim(
    service, runs_kit, scripted_model, replica, monkeypatch
):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    await renew(service.runtime, browser, seconds=600)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        value = Viewer.model_validate(control.lifecycle.reveal(service.runtime, row, "viewer", row.viewer))
        value.expires_at = await now(session) + timedelta(seconds=40)
        row.viewer = control.lifecycle.protect(service.runtime, row, "viewer", value.model_dump(mode="json"))
    entered, finish = asyncio.Event(), asyncio.Event()
    original = supplier.ensure_viewer

    async def delayed(state, grant_id, *, episode, expires_at):
        entered.set()
        await finish.wait()
        grant = await original(state, grant_id, episode=episode, expires_at=expires_at)
        return ViewerGrant(grant.url, grant.state, datetime.now(UTC) + timedelta(seconds=100))

    monkeypatch.setattr(supplier, "ensure_viewer", delayed)
    dispatcher = maintenance.ControlMaintenance(service.runtime)
    await dispatcher()
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await renew(replica(), browser, seconds=900)
        other_dispatcher = maintenance.ControlMaintenance(replica())
        await other_dispatcher()
        await asyncio.gather(*other_dispatcher.inflight)
        assert (await read(replica(), browser)).control_due_at > datetime.now(UTC) + timedelta(seconds=30)
    finally:
        finish.set()
        await asyncio.gather(*dispatcher.inflight)
    async with transaction(replica().storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.control == "human" and row.control_due_at <= await now(session)


async def test_returning_release_keeps_borrower_and_claim_gates_and_suppresses_notices(
    service, runs_kit, scripted_model, monkeypatch
):
    from a13n_service.runs.attempts import AttemptControl
    from a13n_service.runs.execute import execute

    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.control = "returning"
        row.viewer = None
        session.add(
            BrowserBlockedRow(
                id="blocked_release_test",
                browser_id=row.id,
                thread_id=lease.thread_id,
                run_id=lease.run_id,
                generation=row.generation,
                episode=row.episode,
                expires_at=await now(session) + timedelta(minutes=10),
            )
        )
    path = f"{service.api}/browsers/{browser['id']}"

    async def release():
        current = (await service.client.get(path)).json()
        return await service.client.post(f"{path}/release", headers=runs_kit.if_match(current))

    response = await release()
    assert response.status_code == 409
    scripted_model.say("Done")
    await execute(service.runtime, lease, AttemptControl())
    assert (await runs_kit.get_run(service, lease.run_id))["status"] == "completed"
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.claim_id = "claim_test"
        row.claim_until = await now(session) + timedelta(seconds=60)
    response = await release()
    assert response.status_code == 409
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.claim_until = await now(session) - timedelta(seconds=1)
    original = supplier.delete

    async def failed_delete(*args):
        raise TimeoutError("termination unknown")

    monkeypatch.setattr(supplier, "delete", failed_delete)
    response = await release()
    assert response.status_code == 200 and response.json()["status"] == "deleting"
    async with transaction(service.runtime.storage) as session:
        blocked = await session.get_one(BrowserBlockedRow, "blocked_release_test")
        assert blocked.suppressed
    monkeypatch.setattr(supplier, "delete", original)
    await control.lifecycle.reconcile(service.runtime, browser["workspace_id"], browser["id"])
    assert (await read(service.runtime, browser)).status == "deleted"
    assert not supplier.grants


async def test_dispatcher_shutdown_cancels_owned_work_and_stops_admission(service, runs_kit, monkeypatch):
    from a13n_service.infra.tasks import Tasks

    _, browser = await setup(service, runs_kit)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.control = "draining"
    entered = asyncio.Event()

    async def reconcile(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(control, "reconcile", reconcile)
    tasks = Tasks()
    dispatcher = maintenance.ControlMaintenance(replace(service.runtime, tasks=tasks))
    await dispatcher()
    await asyncio.wait_for(entered.wait(), 5)
    await tasks.close(timeout=5)
    assert tasks.closed and not tasks.pending
    await dispatcher()
    assert not dispatcher.inflight
    assert (await read(service.runtime, browser)).control_due_at > datetime.now(UTC)


async def test_failed_revoke_cools_down_all_reconcile_paths(service, runs_kit, scripted_model, replica, monkeypatch):
    from a13n_service.runs.attempts import AttemptControl
    from a13n_service.runs.execute import execute

    supplier, browser = await setup(service, runs_kit)
    run, _ = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=run.thread_id)
    scripted_model.say("Done")
    await execute(service.runtime, run, AttemptControl())
    calls = 0
    original = supplier.revoke_viewer

    async def revoke(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("revoke failed")
        return await original(*args, **kwargs)

    monkeypatch.setattr(supplier, "revoke_viewer", revoke)
    assert (await command(service, runs_kit, browser["id"], "handback"))["control"] == "returning"
    row = await read(service.runtime, browser)
    due = row.control_due_at
    assert row.claim_id is None and due > datetime.now(UTC) + timedelta(seconds=20)
    await control.reconcile(replica(), browser["workspace_id"], browser["id"])
    await command(service, runs_kit, browser["id"], "handback")
    dispatcher = maintenance.ControlMaintenance(replica())
    await dispatcher()
    await asyncio.gather(*dispatcher.inflight)
    assert calls == 1 and (await read(service.runtime, browser)).control_due_at == due
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.control_due_at = await now(session) - timedelta(seconds=1)
    await dispatcher()
    await asyncio.gather(*dispatcher.inflight)
    assert calls == 2 and (await read(service.runtime, browser)).control == "automation"

"""Terminal retirement preserves unknown operations and human-input fences."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.capabilities.browser import BrowserError, BrowserHumanControl
from a13n_service.infra import outbox
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.browsers import admission, control, lifecycle, maintenance
from a13n_service.runs.browsers.tables import BrowserBlockedRow, BrowserOperationRow, BrowserRow
from a13n_service.runs.execute import execute
from sqlalchemy import select

from .test_browser_external import attach
from .test_browser_external import external_connect as external_connect
from .test_browsers import bound_run, command, setup

pytestmark = pytest.mark.anyio


async def complete(service, runs_kit, scripted_model, lease):
    scripted_model.say("Done")
    await execute(service.runtime, lease, AttemptControl())
    assert (await runs_kit.get_run(service, lease.run_id))["status"] == "completed"


@pytest.mark.parametrize("source", ["managed", "external_cdp"])
async def test_unknown_operation_retirement_is_terminal(service, runs_kit, scripted_model, external_connect, source):
    if source == "managed":
        supplier, browser = await setup(service, runs_kit)
    else:
        supplier, _ = external_connect
        browser = await attach(service, runs_kit)
    await command(service, runs_kit, browser["id"], "prepare")
    agent = await runs_kit.create_agent(service, scripted_model)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser, agent=agent)
    permit = await admission.admit(service.runtime, lease, binding)
    await complete(service, runs_kit, scripted_model, lease)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.claim_id = "bclaim_test"
        row.claim_until = datetime.now(UTC) + timedelta(minutes=1)
    current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    response = await service.client.post(
        f"{service.api}/browsers/{browser['id']}/release", headers=runs_kit.if_match(current)
    )
    assert response.status_code == 409 and "browser_operation_pending" in response.text
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.claim_id = row.claim_until = None
        row.idle_seconds = 60
        row.last_used_at = datetime.now(UTC) - timedelta(minutes=2)
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(BrowserRow, browser["id"])).status == "ready"
    assert (await command(service, runs_kit, browser["id"], "release"))["status"] == "deleted"
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(BrowserOperationRow, permit.id)).settled_at is None
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.handle is None
    assert browser["id"] not in supplier.live
    assert supplier.pages == {"original"}
    with pytest.raises(ServiceError):
        await lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"])
    thread = await runs_kit.get_thread(service, lease.thread_id)
    response = await service.client.put(
        f"{service.api}/threads/{lease.thread_id}/browser",
        json=binding.model_dump(mode="json"),
        headers=runs_kit.if_match(thread),
    )
    assert response.status_code == 409, response.text
    response = await runs_kit.submit(service, lease.thread_id, runs_kit.message(agent, "Next"))
    assert response.status_code == 201, response.text
    assert response.json()["run"] is None
    assert response.json()["entry"]["failure"]["code"] == "browser_deleted"
    thread = await runs_kit.get_thread(service, lease.thread_id)
    response = await service.client.put(
        f"{service.api}/threads/{lease.thread_id}/browser", json=None, headers=runs_kit.if_match(thread)
    )
    assert response.status_code == 200, response.text
    response = await runs_kit.submit(service, lease.thread_id, runs_kit.message(agent, "Without browser"))
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("status", ["accepted", "running", "waiting"])
async def test_explicit_retirement_protects_all_active_borrowers(service, runs_kit, scripted_model, status):
    _, browser = await setup(service, runs_kit)
    agent = await runs_kit.create_agent(service, scripted_model, user_questions=True)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser, agent=agent)
    await admission.admit(service.runtime, lease, binding)
    if status == "accepted":
        await complete(service, runs_kit, scripted_model, lease)
        response = await runs_kit.submit(service, lease.thread_id, runs_kit.message(agent, "Next"))
        assert response.status_code == 201 and response.json()["run"]["status"] == "accepted", response.text
    elif status == "waiting":
        scripted_model.call("ask_user_question", {"questions": [runs_kit.QUESTION]}, call_id="call_ask")
        await execute(service.runtime, lease, AttemptControl())
        assert (await runs_kit.get_run(service, lease.run_id))["status"] == "waiting"
    current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    response = await service.client.post(
        f"{service.api}/browsers/{browser['id']}/release", headers=runs_kit.if_match(current)
    )
    assert response.status_code == 409 and "browser_in_use" in response.text


async def test_unknown_takeover_can_cancel_before_grant_then_retire(service, runs_kit, scripted_model):
    supplier, browser = await setup(service, runs_kit)
    await command(service, runs_kit, browser["id"], "prepare")
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    permit = await admission.admit(service.runtime, lease, binding)
    taken = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    assert taken["control"] == "draining" and not supplier.grants
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, lease, binding)
    current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    response = await service.client.post(
        f"{service.api}/browsers/{browser['id']}/release", headers=runs_kit.if_match(current)
    )
    assert response.status_code == 409 and "browser_in_use" in response.text
    returned = await command(service, runs_kit, browser["id"], "handback")
    assert returned["control"] == "automation" and not supplier.grants and not supplier.revoked
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(BrowserOperationRow, permit.id)).settled_at is None
        assert (await session.scalars(select(BrowserBlockedRow))).one().suppressed
    assert not await outbox.claim(
        service.runtime.storage, "browser_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
    )
    with pytest.raises(BrowserError, match="unsettled"):
        await admission.admit(service.runtime, lease, binding)
    await complete(service, runs_kit, scripted_model, lease)
    assert (await command(service, runs_kit, browser["id"], "release"))["status"] == "deleted"


@pytest.mark.parametrize("expired", [False, True])
async def test_cancel_fences_concurrent_inventory_before_viewer_grant(
    service, runs_kit, scripted_model, monkeypatch, expired
):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    entered, finish = asyncio.Event(), asyncio.Event()
    original = control.inventory

    async def inventory(*args):
        pages = await original(*args)
        entered.set()
        await finish.wait()
        return pages

    monkeypatch.setattr(control, "inventory", inventory)
    taking = asyncio.create_task(command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if expired:
            async with transaction(service.runtime.storage) as session:
                (await session.get_one(BrowserRow, browser["id"])).claim_until = datetime.now(UTC) - timedelta(
                    seconds=1
                )
        current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
        response = await service.client.post(
            f"{service.api}/browsers/{browser['id']}/handback", headers=runs_kit.if_match(current)
        )
        if expired:
            assert response.status_code == 200 and response.json()["control"] == "automation", response.text
        else:
            assert response.status_code == 409 and "browser_operation_pending" in response.text
    finally:
        finish.set()
        await taking
    assert bool(supplier.grants) != expired


async def test_persisted_baseline_requires_revoke_not_cancellation(service, runs_kit, scripted_model, monkeypatch):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)

    async def uncertain_grant(state, grant_id, *, expires_at):
        supplier.grants.add(grant_id)
        raise TimeoutError("unknown grant outcome")

    monkeypatch.setattr(supplier, "ensure_viewer", uncertain_grant)
    current = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    assert current["control"] == "returning" and supplier.grants
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.human_baseline is not None and row.viewer is None
    current = await command(service, runs_kit, browser["id"], "handback")
    assert current["control"] == "automation" and not supplier.grants and supplier.revoked


async def test_known_create_failure_requires_explicit_retirement(service, runs_kit, monkeypatch):
    from unittest.mock import AsyncMock

    from a13n_service.providers.browser.definition import BrowserProviderError

    supplier, browser = await setup(service, runs_kit)
    create = AsyncMock(side_effect=BrowserProviderError("rejected", certainty="known"))
    monkeypatch.setattr(supplier, "create", create)
    current = await command(service, runs_kit, browser["id"], "prepare")
    assert current["status"] == "creating" and current["failure"] == "browser_provider_failed"
    current = await command(service, runs_kit, browser["id"], "prepare")
    assert current["status"] == "creating" and current["failure"] == "browser_create_unresolved"
    create.assert_awaited_once()
    assert (await command(service, runs_kit, browser["id"], "release"))["status"] == "deleted"
    response = await service.client.post(
        f"{service.api}/browsers", json={"provider_id": browser["provider_id"], "name": "Replacement"}
    )
    assert response.status_code == 201 and response.json()["id"] != browser["id"], response.text


async def test_explicit_release_preserves_handback_obligation(service, runs_kit, scripted_model):
    _, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, lease, binding)
    await command(service, runs_kit, browser["id"], "handback")
    await complete(service, runs_kit, scripted_model, lease)
    current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    response = await service.client.post(
        f"{service.api}/browsers/{browser['id']}/release", headers=runs_kit.if_match(current)
    )
    assert response.status_code == 409 and "browser_in_use" in response.text


async def test_expired_inventory_cannot_grant_after_cancel_and_retake(service, runs_kit, scripted_model, monkeypatch):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    entered, finish = asyncio.Event(), asyncio.Event()
    original_inventory = control.inventory

    async def inventory(*args):
        pages = await original_inventory(*args)
        entered.set()
        await finish.wait()
        return pages

    monkeypatch.setattr(control, "inventory", inventory)
    taking = asyncio.create_task(command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id))
    retaking = None
    retake_entered, retake_finish = asyncio.Event(), asyncio.Event()
    original_prepare = lifecycle.prepare

    async def prepare(*args, **kwargs):
        retake_entered.set()
        await retake_finish.wait()
        return await original_prepare(*args, **kwargs)

    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(BrowserRow, browser["id"])
            old_grant = row.viewer_id
            row.claim_until = datetime.now(UTC) - timedelta(seconds=1)
        await command(service, runs_kit, browser["id"], "handback")
        monkeypatch.setattr(lifecycle, "prepare", prepare)
        retaking = asyncio.create_task(command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id))
        await asyncio.wait_for(retake_entered.wait(), 5)
        finish.set()
        await taking
        assert not supplier.grants
    finally:
        finish.set()
        retake_finish.set()
        await taking
        if retaking is not None:
            await retaking
    assert supplier.grants and old_grant not in supplier.grants

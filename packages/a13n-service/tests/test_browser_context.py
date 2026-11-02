"""Registration-only handback and read-only availability use current execution authority."""

import asyncio

import pytest
from a13n_harness.capabilities.browser import BrowserHumanControl
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.browsers import admission
from a13n_service.runs.browsers.context import BrowserContextCapability
from a13n_service.runs.browsers.tables import BrowserBlockedRow, BrowserOperationRow, BrowserRow
from sqlalchemy import func, select

from .test_browsers import bound_run, command, setup

pytestmark = pytest.mark.anyio


async def test_context_and_registration_never_prepare_or_admit_page_work(service, runs_kit, scripted_model):
    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    context = BrowserContextCapability(service.runtime, lease, binding)
    assert "browser_notify_on_handback" in context.get_toolset().tools
    for _ in range(2):
        assert await admission.availability(service.runtime, lease, binding) == "not_ready"
        assert (await context.notify_on_handback())["status"] == "not_ready"
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "reserved"
        assert row.observation_epoch == 0
        assert await session.scalar(select(func.count()).select_from(BrowserBlockedRow)) == 0
        assert await session.scalar(select(func.count()).select_from(BrowserOperationRow)) == 0
    assert not supplier.grants


@pytest.mark.parametrize("phase", ["draining", "human", "returning"])
async def test_explicit_registration_coalesces_with_blocked_calls(service, runs_kit, scripted_model, phase):
    _, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.control = phase
        epoch, used = row.observation_epoch, row.last_used_at
    context = BrowserContextCapability(service.runtime, lease, binding)
    assert await admission.availability(service.runtime, lease, binding) == "takeover"
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(BrowserBlockedRow)) == 0
    for _ in range(2):
        assert (await context.notify_on_handback())["status"] == "registered"
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, lease, binding)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert (row.observation_epoch, row.last_used_at) == (epoch, used)
        registrations = list(await session.scalars(select(BrowserBlockedRow)))
        assert len(registrations) == 1 and registrations[0].run_id == lease.run_id
        assert await session.scalar(select(func.count()).select_from(BrowserOperationRow)) == 0
        row.control = "human"
    await command(service, runs_kit, browser["id"], "handback")
    assert (await context.notify_on_handback())["status"] == "already_available"


@pytest.mark.parametrize("first", ["register", "handback"])
async def test_registration_and_handback_arbitrate_on_browser_lock(
    service, runs_kit, scripted_model, monkeypatch, first
):
    _, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    context = BrowserContextCapability(service.runtime, lease, binding)
    if first == "handback":
        await command(service, runs_kit, browser["id"], "handback")
        assert (await context.notify_on_handback())["status"] == "already_available"
    else:
        locked, release = asyncio.Event(), asyncio.Event()
        checked = admission.checked

        async def hold(*args, **kwargs):
            result = await checked(*args, **kwargs)
            locked.set()
            await release.wait()
            return result

        monkeypatch.setattr(admission, "checked", hold)
        registering = asyncio.create_task(context.notify_on_handback())
        await locked.wait()
        returning = asyncio.create_task(command(service, runs_kit, browser["id"], "handback"))
        release.set()
        assert (await registering)["status"] == "registered"
        await returning
    async with short_session(service.runtime.storage) as session:
        rows = list(await session.scalars(select(BrowserBlockedRow)))
        assert len(rows) == (1 if first == "register" else 0)
        if rows:
            assert rows[0].expires_at is not None and not rows[0].suppressed


async def test_binding_change_is_not_reported_as_restoration(service, runs_kit, scripted_model):
    _, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    stale = binding.model_copy(update={"tab_access": "global"})
    with pytest.raises(ServiceError):
        await admission.availability(service.runtime, lease, stale, notify=True)
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(BrowserBlockedRow)) == 0

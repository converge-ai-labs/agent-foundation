"""Retained-profile lifecycle and compatibility, using real durable Service state."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.infra.db import transaction
from a13n_service.runs.browsers import admission, lifecycle, maintenance
from a13n_service.runs.browsers.tables import BrowserRow, BrowserSelectionRow, BrowserTabRow
from sqlalchemy import select

from .test_browser_recovery import complete
from .test_browsers import Supplier, bound_run, command, setup
from .test_browsers import invocation as invocation

pytestmark = pytest.mark.anyio


class RetainedSupplier(Supplier):
    def __init__(self):
        super().__init__()
        self.starts = 0
        self.stops = 0
        self.lose_start = False

    async def create(self, allocation_id, recipe):
        live = await super().create(allocation_id, recipe)
        self.live[allocation_id] = replace(live, runtime_id="runtime-1")
        return self.live[allocation_id]

    async def stop(self, allocation_id, state):
        self.stops += 1
        self.live[allocation_id] = replace(self.live[allocation_id], status="stopped")
        return self.live[allocation_id]

    async def start(self, allocation_id, state, *, generation):
        self.starts += 1
        self.live[allocation_id] = replace(self.live[allocation_id], status="ready", runtime_id=f"runtime-{generation}")
        if self.lose_start:
            raise TimeoutError("start completed but reply lost")
        return self.live[allocation_id]


async def test_retained_defaults_stop_and_next_use_advances_generation(service, runs_kit, scripted_model):
    supplier, browser = await setup(service, runs_kit, supplier=RetainedSupplier(), supports_stop=True)
    assert browser["idle_seconds"] is None
    assert browser["stop_after_seconds"] == 1800
    assert browser["delete_after_seconds"] is None
    await command(service, runs_kit, browser["id"], "prepare")
    agent = await runs_kit.create_agent(service, scripted_model)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser, agent=agent)
    async with transaction(service.runtime.storage) as session:
        session.add(
            BrowserTabRow(
                browser_id=browser["id"],
                workspace_id=browser["workspace_id"],
                thread_id=lease.thread_id,
                target_id="original",
                generation=1,
            )
        )
        session.add(
            BrowserSelectionRow(
                browser_id=browser["id"],
                workspace_id=browser["workspace_id"],
                thread_id=lease.thread_id,
                target_id="original",
                generation=1,
            )
        )
    await complete(service, runs_kit, scripted_model, lease)
    stopped = await command(service, runs_kit, browser["id"], "stop")
    assert stopped["status"] == "stopped"
    assert supplier.stops == 1 and supplier.creates == 1
    async with transaction(service.runtime.storage) as session:
        assert not list(await session.scalars(select(BrowserTabRow)))
        assert not list(await session.scalars(select(BrowserSelectionRow)))
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser, agent=agent)
    permit = await admission.admit(service.runtime, lease, binding)
    assert permit.generation == 1
    permit = await admission.prepare(service.runtime, lease, permit)
    assert permit.generation == 2 and permit.selected_id is None
    selected = await lifecycle.ready_target(service.runtime, browser["workspace_id"], browser["id"])
    assert selected.generation == 2 and supplier.starts == 1 and supplier.creates == 1
    await admission.settle(service.runtime, permit)


async def test_uncertain_start_inspects_without_restarting(service, runs_kit):
    supplier, browser = await setup(service, runs_kit, supplier=RetainedSupplier(), supports_stop=True)
    await command(service, runs_kit, browser["id"], "prepare")
    await command(service, runs_kit, browser["id"], "stop")
    supplier.lose_start = True
    first = await command(service, runs_kit, browser["id"], "prepare")
    assert first["status"] == "starting" and first["generation"] == 2
    second = await command(service, runs_kit, browser["id"], "prepare")
    assert second["status"] == "ready" and second["generation"] == 2 and supplier.starts == 1


@pytest.mark.parametrize("legacy", [False, True])
async def test_idle_stop_and_independent_delete_preserve_last_use(service, runs_kit, legacy):
    policies = {"idle_seconds": 60} if legacy else {"stop_after_seconds": 60, "delete_after_seconds": 180}
    supplier, browser = await setup(service, runs_kit, supplier=RetainedSupplier(), supports_stop=True, **policies)
    await command(service, runs_kit, browser["id"], "prepare")
    old = datetime.now(UTC) - timedelta(seconds=120)
    async with transaction(service.runtime.storage) as session:
        (await session.get_one(BrowserRow, browser["id"])).last_used_at = old
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == ("deleted" if legacy else "stopped")
        assert row.last_used_at == old
        if not legacy:
            row.last_used_at = old - timedelta(seconds=120)
    if not legacy:
        await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
        assert browser["id"] not in supplier.live


async def test_observed_runtime_replacement_invalidates_generation(service, runs_kit):
    supplier, browser = await setup(service, runs_kit, supplier=RetainedSupplier(), supports_stop=True)
    await command(service, runs_kit, browser["id"], "prepare")
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    assert (await command(service, runs_kit, browser["id"], "prepare"))["generation"] == 1
    supplier.live[browser["id"]] = replace(supplier.live[browser["id"]], runtime_id="host-restarted")
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    assert (await command(service, runs_kit, browser["id"], "prepare"))["generation"] == 2


async def test_stop_refuses_unknown_operations_and_active_borrowers(service, runs_kit, scripted_model):
    _, browser = await setup(service, runs_kit, supplier=RetainedSupplier(), supports_stop=True)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    permit = await admission.admit(service.runtime, lease, binding)
    await lifecycle.ready_target(service.runtime, browser["workspace_id"], browser["id"])
    for active in (True, False):
        if not active:
            await complete(service, runs_kit, scripted_model, lease)
        current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
        response = await service.client.post(
            f"{service.api}/browsers/{browser['id']}/stop", headers=runs_kit.if_match(current)
        )
        assert response.status_code == 409 and "browser_in_use" in response.text
    await admission.settle(service.runtime, permit)
    assert (await command(service, runs_kit, browser["id"], "stop"))["status"] == "stopped"


@pytest.mark.parametrize("status", ["reserved", "stopped"])
@pytest.mark.parametrize("preparation", ["first_use", "run_start"])
async def test_paid_refusal_never_publishes_reconcilable_intent(
    service, runs_kit, scripted_model, invocation, status, preparation
):
    from a13n_harness import RunError
    from a13n_service.runs.browsers import execution
    from a13n_service.runs.calls import Refused

    supplier, browser = await setup(service, runs_kit, supplier=RetainedSupplier(), supports_stop=True)
    if status == "stopped":
        await command(service, runs_kit, browser["id"], "prepare")
        await command(service, runs_kit, browser["id"], "stop")
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser, preparation=preparation)
    before = (supplier.creates, supplier.starts)

    async def refuse(**kwargs):
        # Maintenance can run during the external budget check, not only after its refusal.
        await lifecycle.reconcile(service.runtime, browser["workspace_id"], browser["id"])
        assert (supplier.creates, supplier.starts) == before
        raise Refused("budget exhausted")

    invocation.tool_call.side_effect = refuse
    if preparation == "run_start":
        with pytest.raises(Refused, match="budget exhausted"):
            async with execution.open_backend(service.runtime, lease, binding, invocation, frozenset({"tabs"})):
                pytest.fail("Rejected preparation entered the backend")
    else:
        async with execution.open_backend(service.runtime, lease, binding, invocation, frozenset({"tabs"})) as backend:
            with pytest.raises(RunError, match="budget exhausted"):
                await backend.tabs("new")
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    assert (supplier.creates, supplier.starts) == before
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == status and row.generation == 1
        assert not await lifecycle.unsettled(session, row.id)

    # An allowed call still publishes one generation-consistent preparation and permit.
    invocation.tool_call.side_effect = None
    async with execution.open_backend(service.runtime, lease, binding, invocation, frozenset({"tabs"})) as backend:
        await backend.tabs("new")
    assert supplier.creates == 1 and supplier.starts == (1 if status == "stopped" else 0)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "ready" and row.generation == (2 if status == "stopped" else 1)
        assert not await lifecycle.unsettled(session, row.id)

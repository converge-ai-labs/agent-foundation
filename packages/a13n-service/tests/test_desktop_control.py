"""Native-target control through real SQL; provider I/O is an explicit fake guest."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from a13n_harness.environment.models import EnvironmentState
from a13n_service.infra.db import now, transaction
from a13n_service.infra.http import etag
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments.adapters import provider_identity
from a13n_service.runs.environments.desktop import access, control, viewer
from a13n_service.runs.environments.desktop.schemas import OpenViewer, Renew, Takeover
from a13n_service.runs.environments.desktop.tables import DesktopControlRow
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.tenancy.access import principal_for
from pydantic import SecretStr

pytestmark = pytest.mark.anyio
GENERATION = "a" * 32


@pytest.fixture
async def desktop(service, scripted_model, runs_kit, monkeypatch):
    await runs_kit.pause_sweeps(service)
    response = await service.client.post(
        f"{service.api}/environment-providers",
        json={"type": "e2b", "name": "Desktop", "credential": {"api_key": "test-unused-key"}, "config": {}},
    )
    assert response.status_code == 201, response.text
    recipe = {"desktop": True, "desktop_assistance": True, "template": "test-desktop", "execution": "envd_http"}
    response = await service.client.post(
        f"{service.api}/environment-templates",
        json={"name": "Desktop", "provider_id": response.json()["id"], "config": {"recipe": recipe}},
    )
    assert response.status_code == 201, response.text
    response = await service.client.post(f"{service.api}/environments", json={"template_id": response.json()["id"]})
    assert response.status_code == 201, response.text
    environment_id = response.json()["id"]
    async with transaction(service.runtime.storage) as session:
        environment = await session.get(EnvironmentRow, environment_id)
        environment.provider_identity = provider_identity(
            service.runtime.registry, await read_provider(session, EnvironmentProviderRow, environment.provider_id)
        )
        environment.status = "ready"
        environment.operation_id = environment.operation_started_at = None
        environment.handle = Handle(
            recipe=recipe,
            state=EnvironmentState(
                provider_key="e2b",
                state_version="1",
                state={"sandbox_id": "test-" + uuid4().hex},
            ),
        ).model_dump(mode="json")
    agent = await runs_kit.create_agent(service, scripted_model, toolsets={"computer": {"enabled": True}})
    first = await runs_kit.start_thread(
        service,
        agent,
        "Work on the desktop",
        environments=[
            {
                "name": "workspace",
                "environment_id": environment_id,
                "permission_ceiling": {"operations": ["environment.computer.observe", "environment.computer.click"]},
            }
        ],
    )
    calls, grants = [], {}
    state = {"fail_revoke": False, "grant_entered": None, "grant_release": None}

    @asynccontextmanager
    async def connection(runtime, selected, operation_id):
        async def send(request):
            calls.append(request)
            if isinstance(request, wire.Describe):
                return wire.Description(
                    protocol_version=2,
                    generation=GENERATION,
                    port=6081,
                    max_ttl_seconds=60,
                    read_only_enforced=True,
                    revocation_fences_grants=True,
                    websocket_url="wss://desktop.example/ws",
                )
            if isinstance(request, wire.Settle):
                assert not any(item.state == "active" and item.mode == "control" for item in grants.values())
                return wire.Settlement(generation=GENERATION, input_cleanup_complete=True)
            if isinstance(request, wire.Grant):
                if state["grant_entered"] is not None:
                    state["grant_entered"].set()
                    await state["grant_release"].wait()
                if request.id not in grants:
                    grants[request.id] = wire.AccessGrant(
                        id=request.id,
                        generation=GENERATION,
                        epoch=request.epoch,
                        mode=request.mode,
                        state="active",
                        remaining_seconds=60,
                        token=SecretStr("token"),
                        password=SecretStr("password"),
                    )
                return grants[request.id]
            if isinstance(request, wire.Inspect):
                if state["fail_revoke"]:
                    raise RuntimeError("unconfirmed revocation")
                grants[request.id] = grants[request.id].model_copy(
                    update={"state": "revoked", "remaining_seconds": 0, "token": None, "password": None}
                )
                return grants[request.id]
            raise AssertionError(request)

        yield send

    monkeypatch.setattr(access, "connection", connection)
    async with transaction(service.runtime.storage) as session:
        actor = await principal_for(session, service.runtime.access, service.tenant.principal_id)
    return first, environment_id, actor, calls, grants, state


async def begin(service, desktop):
    first, environment_id, actor, *_ = desktop
    initial = await control.get(service.runtime, actor, service.tenant.workspace_id, environment_id)
    return await control.takeover(
        service.runtime,
        actor,
        service.tenant.workspace_id,
        environment_id,
        Takeover(thread_id=first["run"]["thread_id"]),
        if_match=etag(initial.id, initial.version),
    )


async def test_control_renewal_and_verified_return(service, desktop):
    _, environment_id, actor, calls, grants, _ = desktop
    human = await begin(service, desktop)
    assert human.control == "human"
    assert [item.mode for item in grants.values()] == ["control"]
    other = replace(service.runtime)
    long, short = await asyncio.gather(
        *[
            control.renew(
                runtime,
                actor,
                service.tenant.workspace_id,
                environment_id,
                Renew(generation=human.generation, episode=human.episode, lease_seconds=seconds),
            )
            for runtime, seconds in [(service.runtime, 600), (other, 60)]
        ]
    )
    current = await control.get(other, actor, service.tenant.workspace_id, environment_id)
    assert current.expires_at >= max(long.expires_at, short.expires_at)
    granted = await viewer.open_viewer(
        other, actor, service.tenant.workspace_id, environment_id, OpenViewer(mode="control")
    )
    assert granted.password == "password"
    returned = await control.handback(
        other, actor, service.tenant.workspace_id, environment_id, if_match=etag(current.id, current.version)
    )
    assert returned.control == "automation" and returned.expires_at is None
    assert grants[next(iter(grants))].state == "revoked"
    assert isinstance(calls[-1], wire.Settle)


async def test_failed_revoke_retry_is_not_reset_by_repeated_handback(service, desktop):
    _, environment_id, actor, calls, _, state = desktop
    human = await begin(service, desktop)
    state["fail_revoke"] = True
    returning = await control.handback(
        service.runtime, actor, service.tenant.workspace_id, environment_id, if_match=etag(human.id, human.version)
    )
    assert returning.control == "returning" and returning.failure == "desktop_revoke_failed"
    before = len(calls)
    async with transaction(service.runtime.storage) as session:
        due = (await session.get(DesktopControlRow, human.id)).due_at
    await control.handback(
        service.runtime,
        actor,
        service.tenant.workspace_id,
        environment_id,
        if_match=etag(returning.id, returning.version),
    )
    assert len(calls) == before
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopControlRow, human.id)
        assert row.due_at == due
        row.due_at = await now(session) - timedelta(seconds=1)
    state["fail_revoke"] = False
    await control.reconcile(service.runtime, human.id)
    assert (
        await control.get(service.runtime, actor, service.tenant.workspace_id, environment_id)
    ).control == "automation"


async def test_return_during_possible_grant_fences_publication(service, desktop):
    _, environment_id, actor, _, grants, state = desktop
    state["grant_entered"], state["grant_release"] = asyncio.Event(), asyncio.Event()
    pending = asyncio.create_task(begin(service, desktop))
    await asyncio.wait_for(state["grant_entered"].wait(), 5)
    try:
        current = await control.get(service.runtime, actor, service.tenant.workspace_id, environment_id)
        returned = await control.handback(
            service.runtime,
            actor,
            service.tenant.workspace_id,
            environment_id,
            if_match=etag(current.id, current.version),
        )
        assert returned.control == "returning"
    finally:
        state["grant_release"].set()
    result = await asyncio.wait_for(pending, 5)
    assert result.control == "returning"
    await control.reconcile(service.runtime, result.id)
    assert all(item.state == "revoked" for item in grants.values())


async def test_read_only_view_does_not_start_control_lease(service, desktop):
    _, environment_id, actor, _, grants, _ = desktop
    read = await viewer.open_viewer(service.runtime, actor, service.tenant.workspace_id, environment_id, OpenViewer())
    assert read.mode == "view"
    idle = await control.get(service.runtime, actor, service.tenant.workspace_id, environment_id)
    assert idle.control == "automation" and idle.expires_at is None and idle.episode == 0
    human = await begin(service, desktop)
    assert human.control == "human"
    assert {item.mode for item in grants.values()} == {"view", "control"}


async def admitted(service, desktop):
    from a13n_service.runs.claim import claim
    from a13n_service.runs.schemas import EnvironmentMount

    first, environment_id, *_ = desktop
    (lease,) = await claim(service.runtime, worker_id="desktop-worker", worker_build="test", limit=1)
    mount = EnvironmentMount.model_validate(first["run"]["environment_mounts"][0])
    async with transaction(service.runtime.storage) as session:
        environment = await session.get(EnvironmentRow, environment_id)
        state = Handle.model_validate(environment.handle).state
    return lease, mount, state


async def test_computer_drain_blocks_reads_and_coalesces_actual_recipients(service, desktop):
    from a13n_harness.environment.computer import ComputerCall
    from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
    from a13n_service.runs.environments.desktop import admission
    from a13n_service.runs.environments.desktop.tables import DesktopBlockedRow
    from sqlalchemy import select

    lease, mount, state = await admitted(service, desktop)
    call = ComputerCall(EnvironmentAction.COMPUTER_OBSERVE)
    permit = await admission.admit(service.runtime, lease, mount, state, call)
    draining = await begin(service, desktop)
    assert draining.control == "draining" and not desktop[4]
    for _ in range(2):
        with pytest.raises(EnvironmentError, match="human control"):
            await admission.admit(
                service.runtime, lease, mount, state, ComputerCall(EnvironmentAction.COMPUTER_OBSERVE)
            )
    async with transaction(service.runtime.storage) as session:
        registrations = list(await session.scalars(select(DesktopBlockedRow)))
        assert len(registrations) == 1
        assert registrations[0].run_id == lease.run_id
        assert registrations[0].mount == mount.model_dump(mode="json")
    await admission.settle(service.runtime, permit, call)
    async with transaction(service.runtime.storage) as session:
        (await session.get(DesktopControlRow, draining.id)).due_at = None
    await control.reconcile(service.runtime, draining.id)
    assert (await control.get(service.runtime, desktop[2], service.tenant.workspace_id, desktop[1])).control == "human"


@pytest.mark.parametrize("stage", ["pre_dispatch", "dispatched", None])
async def test_provider_error_settles_only_proven_non_dispatch(service, desktop, stage):
    from a13n_harness.environment.computer import ComputerCall
    from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
    from a13n_service.runs.environments.desktop import admission
    from a13n_service.runs.environments.desktop.tables import ComputerOperationRow

    lease, mount, state = await admitted(service, desktop)
    call = ComputerCall(EnvironmentAction.COMPUTER_CLICK)
    permit = await admission.admit(service.runtime, lease, mount, state, call)
    call.dispatched = True
    call.error = EnvironmentError(
        "Provider refused input",
        code="environment_operation_failed",
        details={"dispatch_stage": stage} if stage else {},
    )
    await admission.settle(service.runtime, permit, call)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(ComputerOperationRow, permit.id)
        assert (row.settled_at is not None) == (stage == "pre_dispatch")
        assert row.effect == ("not_executed" if stage == "pre_dispatch" else "unknown")
    assert (await begin(service, desktop)).control == ("human" if stage == "pre_dispatch" else "draining")


async def test_unknown_computer_dispatch_survives_expiry_and_pregrant_cancellation(service, desktop):
    from a13n_harness.environment.computer import ComputerCall
    from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
    from a13n_service.runs.environments.desktop import admission
    from a13n_service.runs.environments.desktop.tables import ComputerOperationRow, DesktopBlockedRow
    from sqlalchemy import select

    lease, mount, state = await admitted(service, desktop)
    call = ComputerCall(EnvironmentAction.COMPUTER_CLICK)
    permit = await admission.admit(service.runtime, lease, mount, state, call)
    call.dispatched = True
    await admission.settle(service.runtime, permit, call)
    draining = await begin(service, desktop)
    with pytest.raises(EnvironmentError):
        await admission.admit(service.runtime, lease, mount, state, ComputerCall(EnvironmentAction.COMPUTER_OBSERVE))
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopControlRow, draining.id)
        row.expires_at = await now(session) - timedelta(seconds=1)
        row.due_at = None
    await control.reconcile(service.runtime, draining.id)
    async with transaction(service.runtime.storage) as session:
        assert (await session.get(DesktopControlRow, draining.id)).control == "automation"
        assert (await session.get(ComputerOperationRow, permit.id)).settled_at is None
        assert (await session.scalar(select(DesktopBlockedRow))).suppressed
    # A later takeover still cannot hand input to a human while the unknown call can act.
    assert (await begin(service, desktop)).control == "draining"
    assert not desktop[4]


@pytest.mark.parametrize("reason", ["lease_expired", "binding_removed"])
async def test_control_sweep_revokes_without_a_live_run(service, desktop, reason):
    from a13n_service.runs.environments.desktop.maintenance import Maintenance
    from a13n_service.runs.environments.tables import ThreadEnvironmentRow
    from sqlalchemy import delete

    first, environment_id, actor, _, grants, _ = desktop
    human = await begin(service, desktop)
    async with transaction(service.runtime.storage) as session:
        row = await session.get(DesktopControlRow, human.id)
        row.due_at = None
        if reason == "lease_expired":
            row.expires_at = await now(session) - timedelta(seconds=1)
        else:
            await session.execute(
                delete(ThreadEnvironmentRow).where(ThreadEnvironmentRow.thread_id == first["run"]["thread_id"])
            )
    sweep = Maintenance(service.runtime)
    await sweep()
    async with asyncio.timeout(5):
        while sweep.inflight:
            await asyncio.sleep(0.01)
    result = await control.get(service.runtime, actor, service.tenant.workspace_id, environment_id)
    assert result.control == "automation"
    assert all(item.state == "revoked" for item in grants.values())


async def test_native_alias_is_retained_by_control_and_read_only_view(service, desktop):
    from a13n_service.runs.environments import lifecycle
    from a13n_service.runs.environments.desktop.tables import DesktopViewerRow
    from sqlalchemy import select, update

    _, environment_id, actor, *_ = desktop
    async with transaction(service.runtime.storage) as session:
        original = await session.get(EnvironmentRow, environment_id)
        template_id, handle, provider = original.template_id, original.handle, original.provider_identity
    response = await service.client.post(f"{service.api}/environments", json={"template_id": template_id})
    assert response.status_code == 201, response.text
    alias_id = response.json()["id"]
    async with transaction(service.runtime.storage) as session:
        alias = await session.get(EnvironmentRow, alias_id)
        alias.status, alias.handle, alias.provider_identity = "ready", handle, provider
        alias.operation_id = alias.operation_started_at = None
    # The alias has no Run. Its native desktop still has to remain alive while a viewer owns it.
    await viewer.open_viewer(service.runtime, actor, service.tenant.workspace_id, environment_id, OpenViewer())
    async with transaction(service.runtime.storage) as session:
        assert await session.scalar(select(lifecycle.in_use(alias_id)))
        await session.execute(update(DesktopViewerRow).values(expires_at=await now(session) - timedelta(seconds=1)))
    async with transaction(service.runtime.storage) as session:
        assert not await session.scalar(select(lifecycle.in_use(alias_id)))
    human = await begin(service, desktop)
    alias_control = await control.get(service.runtime, actor, service.tenant.workspace_id, alias_id)
    assert alias_control.id == human.id
    async with transaction(service.runtime.storage) as session:
        assert await session.scalar(select(lifecycle.in_use(alias_id)))
    await control.handback(
        service.runtime, actor, service.tenant.workspace_id, alias_id, if_match=etag(human.id, human.version)
    )
    async with transaction(service.runtime.storage) as session:
        assert not await session.scalar(select(lifecycle.in_use(alias_id)))


async def test_http_control_is_independent_of_resume_and_keeps_credentials_out_of_metadata(service, desktop, runs_kit):
    first, environment_id, *_ = desktop
    root = f"{service.api}/environments/{environment_id}"
    initial = await service.client.get(root + "/desktop-control")
    assert initial.status_code == 200, initial.text
    assert initial.headers["etag"] == etag(initial.json()["id"], initial.json()["version"])
    body = {"thread_id": first["run"]["thread_id"]}
    missing = await service.client.post(root + "/desktop-control/takeover", json=body)
    assert missing.status_code == 428, missing.text
    human = await service.client.post(
        root + "/desktop-control/takeover", json=body, headers={"If-Match": initial.headers["etag"]}
    )
    assert human.status_code == 200, human.text
    assert human.json()["control"] == "human"
    assert "password" not in human.text and "websocket" not in human.text
    renewed = await service.client.post(
        root + "/desktop-control/renew",
        json={
            "generation": human.json()["generation"],
            "episode": human.json()["episode"],
            "lease_seconds": 600,
        },
    )
    assert renewed.status_code == 200, renewed.text
    opened = await service.client.post(root + "/desktop-viewer", json={"mode": "control"})
    assert opened.status_code == 200, opened.text
    assert opened.headers["cache-control"] == "no-store"
    assert opened.json()["password"] == "password"
    returned = await service.client.post(
        root + "/desktop-control/handback", headers={"If-Match": renewed.headers["etag"]}
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["control"] == "automation"
    run = await runs_kit.get_run(service, first["run"]["id"])
    assert run["status"] == first["run"]["status"] == "accepted"


def test_desktop_control_and_viewer_routes_explicitly_excluded_from_mcp():
    from a13n_service.runs.environments.desktop.routes import router
    from fastapi.routing import APIRoute

    assert len(router.routes) == 5
    assert all(isinstance(route, APIRoute) and route.openapi_extra == {"x-a13n-mcp": False} for route in router.routes)


async def returned_notice(service, desktop):
    from a13n_harness.environment.computer import ComputerCall
    from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
    from a13n_service.infra import outbox
    from a13n_service.runs.environments.desktop import admission

    lease, mount, state = await admitted(service, desktop)
    human = await begin(service, desktop)
    for _ in range(2):
        with pytest.raises(EnvironmentError):
            await admission.admit(
                service.runtime, lease, mount, state, ComputerCall(EnvironmentAction.COMPUTER_OBSERVE)
            )
    await control.handback(
        service.runtime, desktop[2], service.tenant.workspace_id, desktop[1], if_match=etag(human.id, human.version)
    )
    (notice,) = await outbox.claim(
        service.runtime.storage, "desktop_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
    )
    return lease, mount, notice


async def test_handback_delivers_once_to_actual_blocked_run_and_retake_invalidates_input(service, desktop, runs_kit):
    from a13n_service.runs import inbox
    from a13n_service.runs.environments.desktop.delivery import handbacks
    from a13n_service.runs.notices import StaleNotice, require_notice
    from a13n_service.runs.tables import InboxEntryRow, RunRow

    lease, mount, notice = await returned_notice(service, desktop)
    await handbacks(service.runtime)(notice)
    await handbacks(replace(service.runtime))(notice)
    entries = [item for item in await runs_kit.inbox(service, lease.thread_id) if item["kind"] == "desktop_handback"]
    assert len(entries) == 1
    async with transaction(service.runtime.storage) as session:
        entry = await session.get_one(InboxEntryRow, entries[0]["id"])
        run = await session.get_one(RunRow, lease.run_id)
        assert entry.payload["mount"] == mount.model_dump(mode="json")
        assert inbox.steers_into(entry, run, run)
        await require_notice(session, service.runtime, entry)
    await begin(service, desktop)
    async with transaction(service.runtime.storage) as session:
        entry = await session.get_one(InboxEntryRow, entries[0]["id"])
        with pytest.raises(StaleNotice):
            await require_notice(session, service.runtime, entry)


@pytest.mark.parametrize("change", ["binding", "authority", "stop", "archive", "target", "expiry"])
async def test_obsolete_handback_is_suppressed_before_delivery(service, desktop, runs_kit, change):
    from a13n_service.runs.environments.desktop.delivery import handbacks
    from a13n_service.runs.environments.desktop.tables import DesktopBlockedRow
    from a13n_service.runs.environments.tables import ThreadEnvironmentRow
    from a13n_service.runs.notices import suppress
    from a13n_service.runs.tables import ThreadRow

    lease, _, notice = await returned_notice(service, desktop)
    async with transaction(service.runtime.storage) as session:
        if change == "binding":
            binding = await session.get_one(ThreadEnvironmentRow, (lease.thread_id, "workspace"))
            binding.working_directory = "/changed"
        elif change == "authority":
            from .test_platform_tools import set_role

            await set_role(session, service, "viewer")
        elif change == "stop":
            await suppress(session, lease.thread_id)
        elif change == "archive":
            (await session.get_one(ThreadRow, lease.thread_id)).archived_at = await now(session)
        elif change == "target":
            row = await session.get_one(EnvironmentRow, desktop[1])
            handle = Handle.model_validate(row.handle)
            handle.state.state["sandbox_id"] = "replaced-target"
            row.handle = handle.model_dump(mode="json")
        else:
            row = await session.get_one(DesktopBlockedRow, notice.payload["registration_id"])
            row.expires_at = await now(session) - timedelta(seconds=1)
    await handbacks(service.runtime)(notice)
    assert not [item for item in await runs_kit.inbox(service, lease.thread_id) if item["kind"] == "desktop_handback"]
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(DesktopBlockedRow, notice.payload["registration_id"])).suppressed


async def test_idle_handback_inherits_origin_authority_and_mount_without_resume(service, desktop):
    from a13n_service.runs.attempts import AttemptControl
    from a13n_service.runs.environments.desktop.delivery import handbacks
    from a13n_service.runs.execute import _Attempt, _plan
    from a13n_service.runs.schemas import Outcome
    from a13n_service.runs.tables import RunRow, ThreadRow
    from a13n_service.tenancy.authorize import ExecutionAuthority

    lease, mount, notice = await returned_notice(service, desktop)
    plan = await _plan(service.runtime, lease)
    attempt = _Attempt(service.runtime, lease, AttemptControl(), plan)
    attempt.offers.requested = True
    await attempt._commit(
        plan.state,
        cursors={},
        snapshot=attempt._snapshot(),
        outcome=Outcome(status="completed", output="Other work finished"),
    )
    await handbacks(service.runtime)(notice)
    async with transaction(service.runtime.storage) as session:
        thread = await session.get_one(ThreadRow, lease.thread_id)
        successor = await session.get_one(RunRow, thread.current_run_id)
        origin = await session.get_one(RunRow, lease.run_id)
        assert successor.trigger == "desktop_handback"
        assert successor.principal_id == origin.principal_id
        assert ExecutionAuthority.model_validate(successor.authority) == ExecutionAuthority.model_validate(
            origin.authority
        )
        assert mount.model_dump(mode="json") in successor.environment_mounts
        assert successor.resume is None and successor.parent_run_id == origin.id


@pytest.mark.parametrize("mixed", [False, True])
async def test_notice_locks_browser_before_desktop_targets(service, desktop, runs_kit, monkeypatch, mixed):
    from a13n_harness.capabilities.browser import BrowserHumanControl
    from a13n_service.infra import outbox
    from a13n_service.infra.db import lock
    from a13n_service.runs import notices
    from a13n_service.runs.browsers import admission as browser_admission
    from a13n_service.runs.browsers import delivery as browser_delivery
    from a13n_service.runs.browsers import lifecycle
    from a13n_service.runs.browsers.schemas import BrowserBinding
    from a13n_service.runs.browsers.tables import BrowserRow
    from a13n_service.runs.environments.desktop import delivery
    from a13n_service.runs.tables import InboxEntryRow
    from sqlalchemy import select

    from .test_browsers import command, setup

    _, browser = await setup(service, runs_kit)
    binding = BrowserBinding(browser_id=browser["id"])
    first = desktop[0]["run"]
    thread = await runs_kit.get_thread(service, first["thread_id"])
    response = await service.client.put(
        f"{service.api}/threads/{thread['id']}/browser",
        json=binding.model_dump(mode="json"),
        headers=runs_kit.if_match(thread),
    )
    assert response.status_code == 200, response.text
    await service.client.post(f"{service.api}/runs/{first['id']}/interrupt")
    response = await runs_kit.submit(
        service, thread["id"], runs_kit.message({"id": first["agent_id"]}, "Use both targets")
    )
    assert response.status_code == 201, response.text
    thread = await runs_kit.get_thread(service, thread["id"])
    run = await runs_kit.get_run(service, thread["current_run_id"])
    desktop = ({"run": run}, *desktop[1:])
    lease, _, notice = await returned_notice(service, desktop)
    await lifecycle.prepare(service.runtime, service.tenant.workspace_id, browser["id"])
    if mixed:
        await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
        with pytest.raises(BrowserHumanControl):
            await browser_admission.admit(service.runtime, lease, binding)
        await command(service, runs_kit, browser["id"], "handback")
        (browser_notice,) = await outbox.claim(
            service.runtime.storage, "browser_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
        )
        await browser_delivery.handbacks(service.runtime)(browser_notice)
    await delivery.handbacks(service.runtime)(notice)
    acquired = []

    async def record(session, model, identity, **kwargs):
        result = await lock(session, model, identity, **kwargs)
        acquired.append((model, identity))
        return result

    for module in (notices, browser_delivery, delivery):
        monkeypatch.setattr(module, "lock", record)
    async with transaction(service.runtime.storage) as session:
        entries = list(
            await session.scalars(
                select(InboxEntryRow).where(
                    InboxEntryRow.thread_id == lease.thread_id, InboxEntryRow.kind.in_(notices.KINDS)
                )
            )
        )
        assert len(entries) == (2 if mixed else 1)
        await notices.require_notices(session, service.runtime, entries)
    assert acquired[0] == (BrowserRow, browser["id"])
    browser_positions = [index for index, (model, _) in enumerate(acquired) if model is BrowserRow]
    environment_positions = [index for index, (model, _) in enumerate(acquired) if model is EnvironmentRow]
    assert max(browser_positions) < min(environment_positions)


async def test_full_inbox_defers_handback_without_losing_registration(service, desktop, monkeypatch):
    from unittest.mock import AsyncMock

    from a13n_service.infra import outbox
    from a13n_service.runs import inbox
    from a13n_service.runs.environments.desktop.delivery import handbacks
    from a13n_service.runs.environments.desktop.tables import DesktopBlockedRow

    _, _, notice = await returned_notice(service, desktop)
    monkeypatch.setattr(inbox, "has_room", AsyncMock(return_value=False))
    await handbacks(service.runtime)(notice)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(DesktopBlockedRow, notice.payload["registration_id"])
        assert not row.suppressed and row.entry_id is None
        queued = await session.get_one(outbox.OutboxRow, notice.id)
        assert queued.status == "pending" and queued.last_error == "inbox_full"


async def test_pending_output_preserves_historical_desktop_wait_but_cannot_create_one():
    from a13n_service.runs.schemas import HistoricalDesktopPending, Pending
    from pydantic import ValidationError

    old = {"approvals": [], "calls": [], "desktop_control_id": "dtop_old"}
    assert HistoricalDesktopPending.model_validate(old).desktop_control_id == "dtop_old"
    with pytest.raises(ValidationError):
        Pending.model_validate(old)


async def test_old_native_pending_null_is_readable_without_restoring_desktop_execution():
    from a13n_service.runs.schemas import Pending

    native = {
        "approvals": [],
        "calls": [{"tool_call_id": "custom_help", "tool_name": "custom_help", "arguments": {}}],
        "desktop_control_id": None,
    }
    pending = Pending.model_validate(native)
    assert pending.reason == "call"
    assert "desktop_control_id" not in pending.model_dump()


async def test_rebinding_away_and_back_does_not_revive_notice(service, desktop, runs_kit):
    from a13n_service.runs.environments.desktop.delivery import handbacks
    from a13n_service.runs.environments.desktop.tables import DesktopBlockedRow

    lease, mount, notice = await returned_notice(service, desktop)
    path = f"{service.api}/threads/{lease.thread_id}/environments"
    thread = await runs_kit.get_thread(service, lease.thread_id)
    removed = await service.client.delete(path + "/workspace", headers=runs_kit.if_match(thread))
    assert removed.status_code == 204, removed.text
    thread = await runs_kit.get_thread(service, lease.thread_id)
    added = await service.client.post(path, json=mount.model_dump(mode="json"), headers=runs_kit.if_match(thread))
    assert added.status_code == 201, added.text
    await handbacks(service.runtime)(notice)
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(DesktopBlockedRow, notice.payload["registration_id"])).suppressed
    assert not [
        entry for entry in await runs_kit.inbox(service, lease.thread_id) if entry["kind"] == "desktop_handback"
    ]


async def test_pending_handback_retains_native_alias_until_suppressed(service, desktop):
    from a13n_service.runs.environments import lifecycle
    from a13n_service.runs.environments.desktop.delivery import suppress
    from sqlalchemy import select

    lease, _, _ = await returned_notice(service, desktop)
    async with transaction(service.runtime.storage) as session:
        original = await session.get_one(EnvironmentRow, desktop[1])
        template_id, handle, provider = original.template_id, original.handle, original.provider_identity
    response = await service.client.post(f"{service.api}/environments", json={"template_id": template_id})
    assert response.status_code == 201, response.text
    alias_id = response.json()["id"]
    async with transaction(service.runtime.storage) as session:
        alias = await session.get_one(EnvironmentRow, alias_id)
        alias.status, alias.handle, alias.provider_identity = "ready", handle, provider
        alias.operation_id = alias.operation_started_at = None
    async with transaction(service.runtime.storage) as session:
        assert await session.scalar(select(lifecycle.in_use(alias_id)))
        await suppress(session, lease.thread_id)
    async with transaction(service.runtime.storage) as session:
        assert not await session.scalar(select(lifecycle.in_use(alias_id)))


@pytest.mark.parametrize("same_target", [False, True])
async def test_computer_admission_is_scoped_to_native_target_across_threads(service, desktop, runs_kit, same_target):
    from a13n_harness.environment.computer import ComputerCall
    from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
    from a13n_service.runs.environments.desktop import admission
    from a13n_service.runs.environments.desktop.tables import DesktopBlockedRow
    from sqlalchemy import select

    origin, environment_id, actor, *_ = desktop
    await admitted(service, desktop)
    human = await begin(service, desktop)
    async with transaction(service.runtime.storage) as session:
        original = await session.get_one(EnvironmentRow, environment_id)
        template_id, provider = original.template_id, original.provider_identity
        handle = Handle.model_validate(original.handle)
    if not same_target:
        handle.state.state["sandbox_id"] = "independent-desktop"
    response = await service.client.post(f"{service.api}/environments", json={"template_id": template_id})
    assert response.status_code == 201, response.text
    other_id = response.json()["id"]
    async with transaction(service.runtime.storage) as session:
        other = await session.get_one(EnvironmentRow, other_id)
        other.status, other.handle, other.provider_identity = "ready", handle.model_dump(mode="json"), provider
        other.operation_id = other.operation_started_at = None
    other_run = await runs_kit.start_thread(
        service,
        {"id": origin["run"]["agent_id"]},
        "Work independently",
        environments=[
            {
                "name": "workspace",
                "environment_id": other_id,
                "permission_ceiling": {"operations": ["environment.computer.observe"]},
            }
        ],
    )
    lease, mount, state = await admitted(service, (other_run, other_id))
    call = ComputerCall(EnvironmentAction.COMPUTER_OBSERVE)
    if same_target:
        with pytest.raises(EnvironmentError, match="human control"):
            await admission.admit(service.runtime, lease, mount, state, call)
    else:
        permit = await admission.admit(service.runtime, lease, mount, state, call)
        await admission.settle(service.runtime, permit, call)
    async with transaction(service.runtime.storage) as session:
        registrations = list(await session.scalars(select(DesktopBlockedRow)))
        assert len(registrations) == int(same_target)
        if same_target:
            assert registrations[0].thread_id == lease.thread_id
            assert registrations[0].mount["environment_id"] == other_id
    assert (await control.get(service.runtime, actor, service.tenant.workspace_id, environment_id)).id == human.id

"""Managed Browser lifecycle and control against a deterministic supplier and real PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.capabilities.browser import BrowserError, BrowserHumanControl
from a13n_harness.capabilities.browser.cdp import Chromium
from a13n_service.distribution import OSS
from a13n_service.infra import outbox
from a13n_service.infra.db import now, transaction
from a13n_service.providers.browser.definition import BrowserProviderDefinition, LiveBrowser, ViewerGrant
from a13n_service.providers.registry import Registry
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.browsers import admission, control, execution, lifecycle, maintenance
from a13n_service.runs.browsers.delivery import handbacks, require_notice
from a13n_service.runs.browsers.schemas import BrowserBinding
from a13n_service.runs.browsers.tables import (
    BrowserBlockedRow,
    BrowserOperationRow,
    BrowserRow,
    BrowserSelectionRow,
    BrowserTabRow,
)
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from a13n_service.runs.tables import InboxEntryRow
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

pytestmark = pytest.mark.anyio


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Supplier:
    def __init__(self):
        self.creates = 0
        self.live = {}
        self.pages = {"original"}
        self.openers = {}
        self.connections = []
        self.protocol_calls = []
        self.grants = set()
        self.viewer_deadlines = {}
        self.grant_allocations = {}
        self.revoked = set()
        self.lose_create = False
        self.fail_revoke = False
        self.create_entered = asyncio.Event()
        self.create_release = asyncio.Event()
        self.create_release.set()

    async def create(self, allocation_id, recipe):
        self.creates += 1
        self.create_entered.set()
        await self.create_release.wait()
        result = self.live[allocation_id] = LiveBrowser({"id": allocation_id, "secret": "connection-secret"})
        if self.lose_create:
            raise TimeoutError("secret upstream error")
        return result

    async def inspect(self, allocation_id, state):
        return self.live.get(allocation_id)

    async def delete(self, allocation_id, state):
        self.live.pop(allocation_id, None)
        grants = {grant for grant in self.grants if self.grant_allocations[grant] == allocation_id}
        self.revoked.update(grants)
        self.grants.difference_update(grants)

    async def connect(self, state):
        client = Mock(pending_requests={})
        client.stop = AsyncMock()
        client.send_raw = AsyncMock(side_effect=self.send)
        browser = Chromium(client)
        self.connections.append(browser)
        return browser

    async def send(self, method, params, session):
        self.protocol_calls.append((method, params, session))
        if method == "Target.getTargets":
            return {
                "targetInfos": [
                    {
                        "type": "page",
                        "targetId": target,
                        "title": target,
                        "url": "about:blank",
                        **({"openerId": self.openers[target]} if target in self.openers else {}),
                    }
                    for target in sorted(self.pages)
                ]
            }
        if method == "Target.createTarget":
            target = f"new-{len(self.pages)}"
            self.pages.add(target)
            return {"targetId": target}
        if method == "Target.attachToTarget":
            assert params["targetId"] in self.pages
            return {"sessionId": "session-" + params["targetId"]}
        if method == "Target.closeTarget":
            self.pages.remove(params["targetId"])
            return {"success": True}
        assert method in {"Page.enable", "Runtime.enable"}, method
        return {}

    async def ensure_viewer(self, state, grant_id, *, expires_at):
        if state["id"] not in self.live:
            raise ValueError("terminated allocation cannot grant input")
        if grant_id in self.revoked or (
            grant_id in self.viewer_deadlines and self.viewer_deadlines[grant_id] <= datetime.now(UTC)
        ):
            raise ValueError("expired or revoked grants cannot reopen")
        self.grants.add(grant_id)
        self.grant_allocations[grant_id] = state["id"]
        self.viewer_deadlines[grant_id] = max(expires_at, self.viewer_deadlines.get(grant_id, expires_at))
        return ViewerGrant("https://viewer.example/secret", {}, self.viewer_deadlines[grant_id])

    async def revoke_viewer(self, state, grant_id):
        if self.fail_revoke:
            raise TimeoutError("still connected")
        self.revoked.add(grant_id)
        self.grants.discard(grant_id)

    async def close(self):
        pass


async def setup(service, runs_kit):
    await runs_kit.pause_sweeps(service)
    supplier = Supplier()

    async def factory(config, credential, policy):
        return supplier

    definition = BrowserProviderDefinition(
        type="fake_browser",
        display_name="Fake browser",
        configuration_model=Inputs,
        recipe_model=Inputs,
        backend_identity=lambda config: "test",
        provider_factory=factory,
        supports_viewer=True,
    )
    service.runtime = replace(service.runtime, registry=Registry.of((*OSS.providers, definition)))
    service.app.state.runtime = service.runtime
    response = await service.client.post(
        f"{service.api}/browser-providers", json={"type": "fake_browser", "name": "Test"}
    )
    assert response.status_code == 201, response.text
    response = await service.client.post(
        f"{service.api}/browsers", json={"provider_id": response.json()["id"], "name": "Shared"}
    )
    assert response.status_code == 201, response.text
    return supplier, response.json()


async def bound_run(
    service,
    runs_kit,
    scripted_model,
    browser,
    *,
    agent=None,
    preparation="first_use",
    tab_access="thread",
    run_options=None,
    **config,
):
    agent = agent or await runs_kit.create_agent(service, scripted_model, **config)
    binding = BrowserBinding(browser_id=browser["id"], preparation=preparation, tab_access=tab_access)
    started = await runs_kit.start_thread(service, agent, "work")
    thread = await runs_kit.get_thread(service, started["thread"]["id"])
    response = await service.client.put(
        f"{service.api}/threads/{thread['id']}/browser",
        json=binding.model_dump(mode="json"),
        headers=runs_kit.if_match(thread),
    )
    assert response.status_code == 200, response.text
    # The first accepted Run predates the binding. Stop it, then accept from desired state.
    await service.client.post(f"{service.api}/runs/{started['run']['id']}/interrupt")
    response = await runs_kit.submit(
        service, thread["id"], runs_kit.message(agent, "use browser", options=run_options or {})
    )
    assert response.status_code == 201, response.text
    (lease,) = await claim(service.runtime, worker_id="browser-worker", worker_build="test", limit=1)
    return lease, binding


async def command(service, runs_kit, browser_id, name, **body):
    current = await service.client.get(f"{service.api}/browsers/{browser_id}")
    response = await service.client.post(
        f"{service.api}/browsers/{browser_id}/{name}", json=body or None, headers=runs_kit.if_match(current.json())
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_provider_deletion_retains_browser_history(service, runs_kit):
    supplier, browser = await setup(service, runs_kit)
    path = f"{service.api}/browser-providers/{browser['provider_id']}"
    provider = (await service.client.get(path)).json()
    for released in (False, True):
        if released:
            await command(service, runs_kit, browser["id"], "release")
        response = await service.client.delete(path, headers=runs_kit.if_match(provider))
        assert response.status_code == 409, response.text
        assert "provider_in_use" in response.text
    unused = await service.client.post(
        f"{service.api}/browser-providers", json={"type": "fake_browser", "name": "Unused"}
    )
    assert unused.status_code == 201, unused.text
    response = await service.client.delete(
        f"{service.api}/browser-providers/{unused.json()['id']}", headers=runs_kit.if_match(unused.json())
    )
    assert response.status_code == 204, response.text
    assert supplier.creates == 0


async def test_reserved_and_concurrent_prepare_share_one_allocation(service, runs_kit):
    supplier, browser = await setup(service, runs_kit)
    assert browser["status"] == "reserved" and supplier.creates == 0
    supplier.create_release.clear()
    task = asyncio.create_task(lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"]))
    await supplier.create_entered.wait()
    await lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"])
    assert supplier.creates == 1
    supplier.create_release.set()
    await task
    current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    assert current["status"] == "ready" and "secret" not in str(current)


async def test_uncertain_create_inspects_original_without_reallocation(service, runs_kit):
    supplier, browser = await setup(service, runs_kit)
    supplier.lose_create = True
    await lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"])
    current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    assert current["status"] == "creating" and current["failure"] == "browser_provider_unknown"
    await lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"])
    assert supplier.creates == 1
    assert (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()["status"] == "ready"


async def test_takeover_drains_and_coalesces_only_blocked_calls(service, runs_kit, scripted_model):
    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    permit = await admission.admit(service.runtime, lease, binding)
    current = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    assert current["control"] == "draining" and not supplier.grants
    for _ in range(2):
        with pytest.raises(BrowserHumanControl):
            await admission.admit(service.runtime, lease, binding)
    async with transaction(service.runtime.storage) as session:
        assert len(list(await session.scalars(select(BrowserBlockedRow)))) == 1
    await admission.settle(service.runtime, permit)
    await control.reconcile(service.runtime, browser["workspace_id"], browser["id"])
    assert len(supplier.grants) == 1
    supplier.fail_revoke = True
    current = await command(service, runs_kit, browser["id"], "handback")
    assert current["control"] == "returning"
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, lease, binding)
    grant = next(iter(supplier.grants))
    supplier.fail_revoke = False
    await control.reconcile(service.runtime, browser["workspace_id"], browser["id"])
    assert supplier.grants == {grant} and grant not in supplier.revoked
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        current_time = await now(session)
        assert row.control == "returning" and not row.viewer_revoked
        assert row.failure == "browser_viewer_revoke_failed" and row.control_due_at > current_time
        row.control_due_at = current_time - timedelta(seconds=1)
    await control.reconcile(service.runtime, browser["workspace_id"], browser["id"])
    assert not supplier.grants and grant in supplier.revoked
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.control == "automation" and row.viewer_revoked and row.viewer is None
    claims = await outbox.claim(
        service.runtime.storage, "browser_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
    )
    assert len(claims) == 1
    await handbacks(service.runtime)(claims[0])
    await handbacks(service.runtime)(claims[0])
    entries = [entry for entry in await runs_kit.inbox(service, lease.thread_id) if entry["kind"] == "browser_handback"]
    assert len(entries) == 1
    async with transaction(service.runtime.storage) as session:
        await require_notice(session, service.runtime, await session.get_one(InboxEntryRow, entries[0]["id"]))
    # Retake makes even an already-delivered notice stale.
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    from a13n_service.infra.errors import ServiceError

    async with transaction(service.runtime.storage) as session:
        with pytest.raises(ServiceError):
            await require_notice(session, service.runtime, await session.get_one(InboxEntryRow, entries[0]["id"]))


async def test_stop_suppression_survives_later_input(service, runs_kit, scripted_model):
    _, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, lease, binding)
    await service.client.post(f"{service.api}/runs/{lease.run_id}/interrupt")
    await command(service, runs_kit, browser["id"], "handback")
    async with transaction(service.runtime.storage) as session:
        registration = (await session.scalars(select(BrowserBlockedRow))).one()
        assert registration.suppressed
        assert (await session.get_one(BrowserRow, browser["id"])).control == "automation"
    assert (
        await outbox.claim(
            service.runtime.storage, "browser_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
        )
        == []
    )


async def test_agent_native_tools_allocate_lazily_and_leave_tabs_alive(service, runs_kit, scripted_model):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(
        service,
        runs_kit,
        scripted_model,
        browser,
        toolsets={"browser": {"enabled": True, "tools": {"screenshot": {"enabled": False}}}},
    )
    assert supplier.creates == 0
    scripted_model.call("browser_tabs", {"action": "new"}, call_id="call_new")
    scripted_model.call("browser_dialog", {"action": "inspect"}, call_id="call_dialog")
    scripted_model.say("Done")
    await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    run = await runs_kit.get_run(service, lease.run_id)
    assert run["status"] == "completed", run.get("failure")
    request = await scripted_model.request()
    names = {tool["function"]["name"] for tool in request["tools"]}
    assert "browser_tabs" in names and "browser_screenshot" not in names
    assert supplier.creates == 1 and supplier.pages == {"original", "new-1"}
    assert supplier.connections and all(connection.disconnected for connection in supplier.connections)
    assert not any(method == "Target.closeTarget" for method, _, _ in supplier.protocol_calls)
    async with transaction(service.runtime.storage) as session:
        owned = (await session.scalars(select(BrowserTabRow))).one()
        assert owned.target_id == "new-1" and owned.thread_id == lease.thread_id
        assert not await lifecycle.unsettled(session, browser["id"])


@pytest.fixture
def invocation(monkeypatch):
    monkeypatch.setattr(
        execution,
        "current_invocation_scope",
        lambda: SimpleNamespace(invocation=SimpleNamespace(tool_call_id="call_browser", tool_name="browser_tabs")),
    )
    return SimpleNamespace(tool_call=AsyncMock())


async def test_reconnect_restores_exact_selection_and_never_falls_back(service, runs_kit, scripted_model, invocation):
    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    async with execution.open_backend(
        service.runtime, lease, binding, invocation, frozenset({"tabs", "dialog"})
    ) as first:
        assert await first.tabs("list") == ()  # Existing supplier tab is not adopted.
        await first.tabs("new")
        supplier.pages.add("popup")
        supplier.openers["popup"] = "new-1"
        # A second Worker/client restores the same Thread ownership from exact identities.
        async with execution.open_backend(
            service.runtime, lease, binding, invocation, frozenset({"tabs", "dialog"})
        ) as second:
            assert {tab.target for tab in await second.tabs("list")} == {"new-1", "popup"}
            await second.tabs("select", target="popup")
        await first.dialog("inspect")
        assert first.native.page.target == "popup"
        async with transaction(service.runtime.storage) as session:
            selection = await session.get_one(BrowserSelectionRow, (browser["id"], lease.thread_id))
            selection.target_id = None
        # Durable clearing beats a cached native selection.
        await first.tabs("list")
        with pytest.raises(BrowserError, match="No selected"):
            await first.dialog("inspect")
        await first.tabs("select", target="popup")
        supplier.pages.remove("popup")
        with pytest.raises(BrowserError):
            await first.dialog("inspect")
        async with transaction(service.runtime.storage) as session:
            assert (await session.get_one(BrowserSelectionRow, (browser["id"], lease.thread_id))).target_id is None
    assert "original" in supplier.pages and "new-1" in supplier.pages


async def test_permits_read_durable_selection_and_allow_other_pages(service, runs_kit, scripted_model, invocation):
    _, browser = await setup(service, runs_kit)
    agent = await runs_kit.create_agent(service, scripted_model)
    one, binding = await bound_run(service, runs_kit, scripted_model, browser, agent=agent)
    async with execution.open_backend(service.runtime, one, binding, invocation, frozenset({"tabs"})) as backend:
        await backend.tabs("new")
    two, _ = await bound_run(service, runs_kit, scripted_model, browser, agent=agent)
    async with execution.open_backend(service.runtime, two, binding, invocation, frozenset({"tabs"})) as backend:
        await backend.tabs("new")
    first = await admission.admit(service.runtime, one, binding, inventory=False)
    second = await admission.admit(service.runtime, two, binding, inventory=False)
    assert first.selected_id == "new-1" and second.selected_id == "new-2"
    with pytest.raises(BrowserError, match="unsettled"):
        await admission.admit(service.runtime, one, binding, inventory=False)
    with pytest.raises(BrowserError, match="unsettled"):
        await admission.admit(service.runtime, two, binding)
    await admission.settle(service.runtime, first)
    await admission.settle(service.runtime, second)


async def test_unknown_native_creation_is_owned_and_never_timeout_drained(
    service, runs_kit, scripted_model, invocation
):
    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    async with execution.open_backend(service.runtime, lease, binding, invocation, frozenset({"tabs"})) as backend:
        await backend.tabs("list")
        backend.native.browser.navigate = AsyncMock(return_value='{"verification":"timed_out","dispatch":"unknown"}')
        with pytest.raises(BrowserError, match="Tab opened"):
            await backend.tabs("new", url="https://example.com")
        async with transaction(service.runtime.storage) as session:
            assert (await session.scalars(select(BrowserTabRow))).one().target_id == "new-1"
            operation = (
                await session.scalars(select(BrowserOperationRow).where(BrowserOperationRow.settled_at.is_(None)))
            ).one()
            operation.created_at = datetime.now(UTC) - timedelta(days=2)
        current = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
        assert current["control"] == "draining" and not supplier.grants


async def test_run_start_during_human_control_does_not_register_a_fake_call(
    service, runs_kit, scripted_model, invocation
):
    _, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser, preparation="run_start")
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    async with execution.open_backend(service.runtime, lease, binding, invocation, frozenset({"tabs"})):
        pass
    async with transaction(service.runtime.storage) as session:
        assert list(await session.scalars(select(BrowserBlockedRow))) == []
    invocation.tool_call.assert_not_awaited()


async def test_blocked_native_tool_returns_guidance_and_model_continues(service, runs_kit, scripted_model):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser, toolsets={"browser": {"enabled": True}})
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    before = len(supplier.protocol_calls)
    scripted_model.call("browser_tabs", {"action": "new"}, call_id="call_blocked")
    scripted_model.say("I can continue unrelated work")
    await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    run = await runs_kit.get_run(service, lease.run_id)
    assert run["status"] == "completed", run.get("failure")
    await scripted_model.request()
    after_tool = await scripted_model.request()
    assert "do not poll or retry" in str(after_tool["messages"])
    assert len(supplier.protocol_calls) == before and supplier.pages == {"original"}
    async with transaction(service.runtime.storage) as session:
        assert len(list(await session.scalars(select(BrowserBlockedRow)))) == 1


async def test_paid_call_refusal_precedes_first_allocation(service, runs_kit, scripted_model, invocation):
    from a13n_harness import RunError
    from a13n_service.runs.calls import Refused

    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    invocation.tool_call.side_effect = Refused("budget exhausted")
    async with execution.open_backend(service.runtime, lease, binding, invocation, frozenset({"tabs"})) as backend:
        with pytest.raises(RunError, match="budget exhausted"):
            await backend.tabs("new")
    assert supplier.creates == 0
    invocation.tool_call.assert_awaited_once_with(
        call_id="call_browser", source="browser", tool_name="browser_tabs", provider_id=browser["provider_id"]
    )
    async with transaction(service.runtime.storage) as session:
        assert not await lifecycle.unsettled(session, browser["id"])


async def test_maintenance_recovers_create_and_releases_idle_browser(service, runs_kit):
    supplier, browser = await setup(service, runs_kit)
    supplier.lose_create = True
    await lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"])
    await maintenance.Maintenance(service.runtime)()
    assert supplier.creates == 1
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "ready"
        row.last_used_at = datetime.now(UTC) - timedelta(hours=1)
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    assert not supplier.live
    current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    assert current["status"] == "deleted"


async def test_release_unknown_create_cleans_original_identity(service, runs_kit):
    supplier, browser = await setup(service, runs_kit)
    supplier.lose_create = True
    await lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"])
    assert browser["id"] in supplier.live
    current = await command(service, runs_kit, browser["id"], "release")
    assert current["status"] == "deleted" and not supplier.live
    await maintenance.Maintenance(service.runtime)()
    assert supplier.creates == 1


@pytest.mark.parametrize("cause", ["provider_disabled", "viewer_expired"])
async def test_viewer_failure_safely_hands_back_after_revocation(service, runs_kit, scripted_model, cause):
    from a13n_service.resources.providers.tables import BrowserProviderRow

    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    assert supplier.grants
    async with transaction(service.runtime.storage) as session:
        if cause == "provider_disabled":
            (await session.get_one(BrowserProviderRow, browser["provider_id"])).enabled = False
        else:
            row = await session.get_one(BrowserRow, browser["id"])
            value = lifecycle.reveal(service.runtime, row, "viewer", row.viewer)
            value["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            row.viewer = lifecycle.protect(service.runtime, row, "viewer", value)
    supplier.fail_revoke = True
    await control.reconcile(service.runtime, browser["workspace_id"], browser["id"])
    assert supplier.grants
    grant = next(iter(supplier.grants))
    if cause == "provider_disabled":
        from a13n_service.infra.errors import ServiceError

        with pytest.raises(ServiceError) as error:
            await admission.admit(service.runtime, lease, binding)
        assert error.value.code == "disabled"
    else:
        with pytest.raises(BrowserHumanControl):
            await admission.admit(service.runtime, lease, binding)
    supplier.fail_revoke = False
    await control.reconcile(service.runtime, browser["workspace_id"], browser["id"])
    assert supplier.grants == {grant} and grant not in supplier.revoked
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        current_time = await now(session)
        assert row.control == "returning" and not row.viewer_revoked
        assert row.failure == "browser_viewer_revoke_failed" and row.control_due_at > current_time
        row.control_due_at = current_time - timedelta(seconds=1)
    await control.reconcile(service.runtime, browser["workspace_id"], browser["id"])
    assert not supplier.grants and grant in supplier.revoked
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.control == "automation" and row.viewer_revoked and row.viewer is None
    # Handback remains idempotent after disablement; cleanup does not need new-use permission.
    current = await command(service, runs_kit, browser["id"], "handback")
    assert current["control"] == "automation"


async def test_expiry_invalidates_handback_without_settling_unknown_operations(service, runs_kit, scripted_model):
    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    permit = await admission.admit(service.runtime, lease, binding)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, lease, binding)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(BrowserRow, browser["id"])).status == "lost"
        assert (await session.get_one(BrowserOperationRow, permit.id)).settled_at is None
        assert (await session.scalars(select(BrowserBlockedRow))).one().suppressed
    current = await command(service, runs_kit, browser["id"], "release")
    assert current["status"] == "deleted" and not supplier.live and not supplier.grants


async def test_pending_handback_pins_browser_until_consumed_or_expired(service, runs_kit, scripted_model, monkeypatch):
    from a13n_service.runs import accept

    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser, toolsets={"browser": {"enabled": True}})
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    scripted_model.call("browser_tabs", {"action": "list"}, call_id="blocked")
    scripted_model.say("Other work complete")
    await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    assert (await runs_kit.get_run(service, lease.run_id))["status"] == "completed"
    await command(service, runs_kit, browser["id"], "handback")
    # Lose the post-commit wakeup, leaving only the durable pending inbox entry.
    monkeypatch.setattr(accept, "advance", AsyncMock())
    (delivery,) = await outbox.claim(
        service.runtime.storage, "browser_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
    )
    await handbacks(service.runtime)(delivery)
    async with transaction(service.runtime.storage) as session:
        registration = (await session.scalars(select(BrowserBlockedRow))).one()
        assert (await session.get_one(InboxEntryRow, registration.entry_id)).status == "pending"
        row = await session.get_one(BrowserRow, browser["id"])
        row.last_used_at = datetime.now(UTC) - timedelta(hours=1)
        assert await lifecycle.in_use(session, row)
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    assert supplier.live
    async with transaction(service.runtime.storage) as session:
        registration = (await session.scalars(select(BrowserBlockedRow))).one()
        registration.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    assert not supplier.live


async def accepted_handback(service, runs_kit, scripted_model, **config):
    supplier, browser = await setup(service, runs_kit)
    origin, binding = await bound_run(service, runs_kit, scripted_model, browser, **config)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=origin.thread_id)
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, origin, binding)
    scripted_model.say("Unrelated work done")
    await execute(service.runtime, origin, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    assert (await runs_kit.get_run(service, origin.run_id))["status"] == "completed"
    await scripted_model.request()
    await command(service, runs_kit, browser["id"], "handback")
    (delivery,) = await outbox.claim(
        service.runtime.storage, "browser_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
    )
    await handbacks(service.runtime)(delivery)
    (lease,) = await claim(service.runtime, worker_id="notice-worker", worker_build="test", limit=1)
    return supplier, browser, lease


@pytest.mark.parametrize("retake_at", ["before_read", "before_commit"])
async def test_obsolete_handback_completes_without_model_or_thread_pause(
    service, runs_kit, scripted_model, monkeypatch, retake_at
):
    from a13n_service.runs import checkpoints

    _, browser, lease = await accepted_handback(service, runs_kit, scripted_model)
    if retake_at == "before_read":
        await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    else:
        publish = checkpoints.publish_checkpoint
        retaken = False

        async def retake_before_commit(*args, **kwargs):
            nonlocal retaken
            result = await publish(*args, **kwargs)
            if not retaken:
                retaken = True
                await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
            return result

        monkeypatch.setattr(checkpoints, "publish_checkpoint", retake_before_commit)
    await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    run = await runs_kit.get_run(service, lease.run_id)
    assert run["status"] == "accepted", run.get("failure")
    async with transaction(service.runtime.storage) as session:
        registration = (await session.scalars(select(BrowserBlockedRow))).one()
        entry = await session.get_one(InboxEntryRow, registration.entry_id)
        assert entry.status == "failed" and entry.failure["code"] == "browser_notice_stale"
    (recovered,) = await claim(service.runtime, worker_id="replacement", worker_build="test", limit=1)
    assert recovered.run_id == lease.run_id
    await execute(service.runtime, recovered, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    run = await runs_kit.get_run(service, lease.run_id)
    assert run["status"] == "completed", run.get("failure")
    assert run["usage_at_seal"]["requests"] == 0
    thread = await runs_kit.get_thread(service, lease.thread_id)
    assert thread["current_run_id"] is None
    # No failed/cancelled Run pauses automatic advancement after the obsolete notice.
    from a13n_service.runs.accept import paused
    from a13n_service.runs.tables import RunRow

    async with transaction(service.runtime.storage) as session:
        stored = await session.get_one(RunRow, lease.run_id)
        assert not paused(stored)
        pointer = checkpoints.require_compatible(stored)
    state = await checkpoints.load_state(service.runtime.objects, pointer)
    assert "Human control ended" not in str(state.harness.message_history)


async def test_handback_committed_before_retake_remains_a_historical_observation(service, runs_kit, scripted_model):
    _, browser, lease = await accepted_handback(service, runs_kit, scripted_model)
    gate = asyncio.Event()
    scripted_model.say("I will observe before acting", gate=gate)
    running = asyncio.create_task(
        execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    )
    request = await scripted_model.request()
    assert "Human control ended" in str(request["messages"])
    async with transaction(service.runtime.storage) as session:
        registration = (await session.scalars(select(BrowserBlockedRow))).one()
        assert (await session.get_one(InboxEntryRow, registration.entry_id)).status == "consumed"
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    gate.set()
    await running
    assert (await runs_kit.get_run(service, lease.run_id))["status"] == "completed"


async def test_ready_inspection_distinguishes_unavailable_from_resource_loss(
    service, runs_kit, scripted_model, monkeypatch
):
    supplier, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    permit = await admission.admit(service.runtime, lease, binding)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, lease, binding)
    inspect = supplier.inspect
    monkeypatch.setattr(supplier, "inspect", AsyncMock(side_effect=TimeoutError))
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    assert (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()["status"] == "ready"
    monkeypatch.setattr(supplier, "inspect", inspect)
    supplier.live.clear()
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "lost" and row.failure == "browser_lost"
        assert (await session.get_one(BrowserOperationRow, permit.id)).settled_at is None
        assert (await session.scalars(select(BrowserBlockedRow))).one().suppressed
    assert supplier.creates == 1


async def test_handback_before_readiness_cancels_control_intent_without_availability_notice(
    service, runs_kit, scripted_model
):
    supplier, browser = await setup(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    supplier.lose_create = True
    current = await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    assert current["status"] == "creating" and current["control"] == "draining"
    current = await command(service, runs_kit, browser["id"], "handback")
    assert current["control"] == "automation" and not supplier.grants
    assert not await outbox.claim(
        service.runtime.storage, "browser_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
    )
    scripted_model.say("Done")
    await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    current = await command(service, runs_kit, browser["id"], "release")
    assert current["status"] == "deleted" and not supplier.live


@pytest.mark.parametrize("retake_at", ["before_read", "before_commit"])
async def test_discarding_stale_source_preserves_other_assigned_work(
    service, runs_kit, scripted_model, monkeypatch, retake_at
):
    from a13n_service.runs import checkpoints
    from a13n_service.runs.tables import RunRow

    _, browser, lease = await accepted_handback(service, runs_kit, scripted_model)
    run = await runs_kit.get_run(service, lease.run_id)
    message = await runs_kit.submit(
        service, lease.thread_id, runs_kit.message({"id": run["agent_id"]}, "Keep doing this real work")
    )
    assert message.status_code == 201, message.text
    entry_id = message.json()["entry"]["id"]
    # Recover the ordinary boundary assignment alongside the notification source.
    async with transaction(service.runtime.storage) as session:
        entry = await session.get_one(InboxEntryRow, entry_id)
        entry.status, entry.assigned_run_id = "assigned", lease.run_id
    if retake_at == "before_read":
        await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    else:
        publish = checkpoints.publish_checkpoint
        retaken = False

        async def retake_before_commit(*args, **kwargs):
            nonlocal retaken
            result = await publish(*args, **kwargs)
            if not retaken:
                retaken = True
                await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
            return result

        monkeypatch.setattr(checkpoints, "publish_checkpoint", retake_before_commit)
    await execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    (recovered,) = await claim(service.runtime, worker_id="replacement", worker_build="test", limit=1)
    scripted_model.say("Real work complete")
    await execute(service.runtime, recovered, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    request = await scripted_model.request()
    assert "Keep doing this real work" in str(request["messages"])
    assert "Human control ended" not in str(request["messages"])
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(InboxEntryRow, entry_id)).status == "consumed"
        assert (await session.get_one(RunRow, lease.run_id)).status == "completed"


async def test_stale_handback_keeps_completed_inline_delegate(service, runs_kit, scripted_model, monkeypatch):
    from a13n_service.runs import checkpoints
    from a13n_service.runs.tables import RunRow

    _, browser = await setup(service, runs_kit)
    agent = await runs_kit.delegating(service, scripted_model, "inline")
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser, agent=agent)
    await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
    with pytest.raises(BrowserHumanControl):
        await admission.admit(service.runtime, lease, binding)
    gate = asyncio.Event()
    scripted_model.call(
        "delegate",
        {"subagent": "helper", "prompt": "compute"},
        call_id="completed-child",
        to="Role: coordinator",
        gate=gate,
    )
    scripted_model.say("Unique completed child result", to="Role: worker")
    scripted_model.say("Finished", to="Role: coordinator")
    publish = checkpoints.publish_checkpoint
    retaken = False

    async def retake_before_commit(*args, **kwargs):
        nonlocal retaken
        result = await publish(*args, **kwargs)
        state = args[2].harness
        if not retaken and "Human control ended" in str(state.message_history):
            assert "Unique completed child result" in str(state.message_history)
            retaken = True
            await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
        return result

    monkeypatch.setattr(checkpoints, "publish_checkpoint", retake_before_commit)
    running = asyncio.create_task(
        execute(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    )
    await scripted_model.request()
    await command(service, runs_kit, browser["id"], "handback")
    (delivery,) = await outbox.claim(
        service.runtime.storage, "browser_handback", owner="test", limit=10, lease_seconds=60, max_attempts=12
    )
    await handbacks(service.runtime)(delivery)
    gate.set()
    await running
    assert retaken
    async with transaction(service.runtime.storage) as session:
        stored = await session.get_one(RunRow, lease.run_id)
        assert stored.status == "accepted"
        pointer = checkpoints.require_compatible(stored)
    state = await checkpoints.load_state(service.runtime.objects, pointer)
    assert "Unique completed child result" in str(state.harness.message_history)
    assert "Human control ended" not in state.harness.model_dump_json()
    (recovered,) = await claim(service.runtime, worker_id="replacement", worker_build="test", limit=1)
    await execute(service.runtime, recovered, AttemptControl(deadline=asyncio.get_running_loop().time() + 60))
    assert (await runs_kit.get_run(service, lease.run_id))["status"] == "completed"
    requests = [scripted_model.requests.get_nowait() for _ in range(scripted_model.requests.qsize())]
    assert len(requests) == 2  # One child, then the recovered coordinator; no replay.
    assert "Unique completed child result" in str(requests[-1]["messages"])
    assert "Human control ended" not in str(requests[-1]["messages"])


@pytest.mark.parametrize("handoff", [False, True])
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("retake_first", [False, True])
async def test_handback_is_committed_before_compaction_can_observe_it(
    service, runs_kit, scripted_model, monkeypatch, background, retake_first, handoff
):
    from a13n_harness.capabilities import context
    from a13n_service.runs import checkpoints
    from a13n_service.runs.execute import _Attempt, _plan
    from a13n_service.runs.tables import RunRow

    _, browser, lease = await accepted_handback(
        service,
        runs_kit,
        scripted_model,
        background_compaction={"enabled": background},
        model_characteristics={"context_window_tokens": 10},
        summarize=handoff,
    )
    plan = await _plan(service.runtime, lease)

    async def summarize(ctx, request):
        # Once incorporated, later retake leaves the event as a historical observation.
        async with transaction(service.runtime.storage) as session:
            registration = (await session.scalars(select(BrowserBlockedRow))).one()
            assert (await session.get_one(InboxEntryRow, registration.entry_id)).status == "consumed"
        await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
        return "Human control ended; the browser is available again."

    summary = AsyncMock(side_effect=summarize)
    scripted_model.say("Observe current state before acting")
    monkeypatch.setattr(context, "_compact_with_same_agent", summary)
    # The previous scripted response reports 12 input tokens, exceeding both
    # compaction and background blocking thresholds for the ten-token window.
    fallback = context.CompactionCapability._foreground_fallback
    fallback_calls = []

    async def record_fallback(capability, ctx, request):
        fallback_calls.append(True)
        return await fallback(capability, ctx, request)

    monkeypatch.setattr(context.CompactionCapability, "_foreground_fallback", record_fallback)
    publish = checkpoints.publish_checkpoint
    retaken = False

    async def retake_before_commit(*args, **kwargs):
        nonlocal retaken
        result = await publish(*args, **kwargs)
        if retake_first and not retaken:
            retaken = True
            await command(service, runs_kit, browser["id"], "takeover", thread_id=lease.thread_id)
        return result

    monkeypatch.setattr(checkpoints, "publish_checkpoint", retake_before_commit)
    await _Attempt(service.runtime, lease, AttemptControl(deadline=asyncio.get_running_loop().time() + 60), plan).run()
    assert retaken == retake_first
    async with transaction(service.runtime.storage) as session:
        stored = await session.get_one(RunRow, lease.run_id)
        assert stored.status == ("accepted" if retake_first else "completed")
        pointer = checkpoints.require_compatible(stored)
        registration = (await session.scalars(select(BrowserBlockedRow))).one()
        assert (await session.get_one(InboxEntryRow, registration.entry_id)).status == (
            "failed" if retake_first else "consumed"
        )
    state = await checkpoints.load_state(service.runtime.objects, pointer)
    assert ("Human control ended" in state.harness.model_dump_json()) is not retake_first
    assert summary.await_count == (0 if retake_first else 1)
    assert bool(fallback_calls) is (background and not retake_first)

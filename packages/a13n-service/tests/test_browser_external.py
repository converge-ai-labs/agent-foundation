"""External attachment uses Browser authority/lifetimes without a supplier account."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from a13n_harness import RunConfiguration
from a13n_harness.capabilities.browser import BrowserError
from a13n_harness.capabilities.browser.cdp import Chromium
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.infra.db import transaction
from a13n_service.infra.outbound import validate_cdp_endpoint
from a13n_service.runs.attempts import AuthorityRevoked
from a13n_service.runs.browsers import admission, execution, lifecycle, maintenance
from a13n_service.runs.browsers.tables import BrowserRow
from a13n_service.tenancy.tables import GrantRow
from sqlalchemy import delete

from .test_browsers import Supplier, bound_run, command
from .test_browsers import invocation as invocation

pytestmark = pytest.mark.anyio
URL = "wss://browser.example/devtools/browser/existing?token=secret-url-token"
HEADERS = {"Authorization": "Bearer secret-header-token"}


async def attach(service, runs_kit, **fields):
    await runs_kit.pause_sweeps(service)
    response = await service.client.post(
        f"{service.api}/browsers/attach", json={"name": "Existing", "cdp_url": URL, "headers": HEADERS, **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def external_connect(monkeypatch):
    supplier = Supplier()
    original = supplier.send

    async def send(method, params, session):
        if method == "Target.getBrowserContexts":
            return {"browserContextIds": []}
        return await original(method, params, session)

    supplier.send = send

    async def connect(endpoint, **kwargs):
        assert endpoint == URL
        assert kwargs["headers"] == {"authorization": HEADERS["Authorization"]}
        assert not kwargs["discover"]
        assert kwargs["timeout"] > 0
        return await supplier.connect({})

    mock = AsyncMock(side_effect=connect)
    monkeypatch.setattr(Chromium, "connect", mock)
    return supplier, mock


@pytest.mark.parametrize("prepared", [False, True])
async def test_external_registration_encryption_and_release(service, runs_kit, external_connect, prepared, caplog):
    supplier, connect = external_connect
    browser = await attach(service, runs_kit, prepare=prepared)
    assert browser["source"] == "external_cdp" and browser["provider_id"] is None
    assert browser["idle_seconds"] is None
    assert browser["status"] == ("ready" if prepared else "reserved")
    assert connect.await_count == int(prepared)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert "secret" not in json.dumps(row.handle)
        assert lifecycle.reveal(service.runtime, row, "handle", row.handle) == {
            "cdp_url": URL,
            "headers": {"authorization": HEADERS["Authorization"]},
        }
    for path in (f"/browsers/{browser['id']}", "/browsers"):
        response = await service.client.get(service.api + path)
        assert response.status_code == 200
        assert "secret" not in response.text and "cdp_url" not in response.text
    released = await command(service, runs_kit, browser["id"], "release")
    assert released["status"] == "deleted"
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(BrowserRow, browser["id"])).handle is None
    assert supplier.pages == {"original"} and connect.await_count == int(prepared)
    assert "secret-url-token" not in caplog.text and "secret-header-token" not in caplog.text


@pytest.mark.parametrize(
    "fields",
    [
        {"cdp_url": "https://browser.example/json/version?token=secret"},
        {"cdp_url": "ws://browser.example/devtools/browser/id?token=secret"},
        {"cdp_url": "wss://user:secret@browser.example/devtools/browser/id"},
        {"cdp_url": "wss://browser.example/devtools/page/id?token=secret"},
        {"cdp_url": "wss://browser.example:invalid/id?token=secret"},
        {"cdp_url": URL + "#secret"},
        {"headers": {"Host": "secret"}},
        {"headers": {"X-Test": "secret\r\nInjected: secret"}},
    ],
)
async def test_external_invalid_inputs_are_secret_free(service, runs_kit, external_connect, fields):
    await runs_kit.pause_sweeps(service)
    _, connect = external_connect
    response = await service.client.post(
        f"{service.api}/browsers/attach", json={"name": "Existing", "cdp_url": URL, **fields}
    )
    assert response.status_code == 400, response.text
    assert "secret" not in response.text
    connect.assert_not_awaited()


async def test_cdp_policy_separates_secret_query_without_weakening_http_rules():
    policy = EndpointPolicy.from_http_origins(require_https=True, http_origins=["http://127.0.0.1:9222"])
    await validate_cdp_endpoint(policy, "ws://127.0.0.1:9222/devtools/browser/id?token=secret")
    await validate_cdp_endpoint(policy, URL)
    for endpoint in ("ws://127.0.0.1:9223/devtools/browser/id", "ws://browser.example/id"):
        with pytest.raises(EndpointPolicyError):
            await validate_cdp_endpoint(policy, endpoint)
    with pytest.raises(EndpointPolicyError):
        await policy.validate("https://browser.example/id?token=secret")
    with pytest.raises(EndpointPolicyError):
        await validate_cdp_endpoint(policy.for_run(RunConfiguration(allowed_hosts=frozenset())), URL)


async def test_external_control_rejected_without_transition(service, runs_kit, scripted_model, external_connect):
    _, connect = external_connect
    browser = await attach(service, runs_kit)
    lease, _ = await bound_run(service, runs_kit, scripted_model, browser)
    browser = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    for operation in ("takeover", "viewer", "handback"):
        response = await service.client.post(
            f"{service.api}/browsers/{browser['id']}/{operation}",
            json={"thread_id": lease.thread_id} if operation == "takeover" else None,
            headers=runs_kit.if_match(browser),
        )
        assert response.status_code == 409, response.text
        assert "browser_viewer_unsupported" in response.text
    current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    assert current["control"] == "automation" and current["episode"] == 0
    assert current["version"] == browser["version"]
    connect.assert_not_awaited()


async def test_external_connection_failure_retries_same_attachment(service, runs_kit, external_connect):
    supplier, connect = external_connect
    success = connect.side_effect
    connect.side_effect = TimeoutError("secret upstream URL " + URL)
    browser = await attach(service, runs_kit, prepare=True)
    assert browser["status"] == "creating" and browser["failure"] == "browser_connection_failed"
    assert "secret" not in json.dumps(browser)
    connect.side_effect = success
    current = await command(service, runs_kit, browser["id"], "prepare")
    assert current["status"] == "ready" and current["generation"] == 1
    connect.side_effect = OSError("secret upstream headers")
    current = await command(service, runs_kit, browser["id"], "prepare")
    assert current["status"] == "ready" and current["failure"] == "browser_connection_failed"
    connect.side_effect = success
    await lifecycle.reconcile(service.runtime, browser["workspace_id"], browser["id"], inspect_ready=True)
    assert supplier.pages == {"original"}


@pytest.mark.parametrize("prepared", [False, True])
async def test_external_idle_forgets_secret_without_closing(service, runs_kit, external_connect, prepared):
    supplier, connect = external_connect
    browser = await attach(service, runs_kit, prepare=prepared, idle_seconds=60)
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        row.last_used_at = datetime.now(UTC) - timedelta(minutes=2)
    await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "deleted" and row.handle is None
    assert supplier.pages == {"original"} and connect.await_count == int(prepared)


async def test_external_admission_frozen_hosts_and_release_protection(
    service, runs_kit, scripted_model, external_connect, invocation
):
    _, connect = external_connect
    browser = await attach(service, runs_kit)
    lease, binding = await bound_run(
        service, runs_kit, scripted_model, browser, run_options={"configuration": {"allowed_hosts": []}}
    )
    browser = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    response = await service.client.post(
        f"{service.api}/browsers/{browser['id']}/release", headers=runs_kit.if_match(browser)
    )
    assert response.status_code == 409 and "browser_in_use" in response.text
    async with execution.open_backend(service.runtime, lease, binding, invocation, frozenset({"tabs"})) as backend:
        with pytest.raises(BrowserError):
            await backend.tabs("list")
    connect.assert_not_awaited()
    async with transaction(service.runtime.storage) as session:
        assert not await lifecycle.unsettled(session, browser["id"])
        await session.execute(delete(GrantRow).where(GrantRow.principal_id == service.tenant.principal_id))
    with pytest.raises(AuthorityRevoked):
        await admission.admit(service.runtime, lease, binding)
    connect.assert_not_awaited()


@pytest.mark.parametrize("role, permitted", [("viewer", False), ("runner", True)])
async def test_external_attach_requires_run_but_release_requires_write(
    service, runs_kit, external_connect, role, permitted
):
    from a13n_service.infra.ids import new_object_id

    await runs_kit.pause_sweeps(service)
    async with transaction(service.runtime.storage) as session:
        await session.execute(delete(GrantRow).where(GrantRow.principal_id == service.tenant.principal_id))
        session.add(
            GrantRow(
                id=new_object_id("gr"),
                organization_id=service.tenant.organization_id,
                workspace_id=service.tenant.workspace_id,
                principal_id=service.tenant.principal_id,
                role=role,
                created_by_id=service.tenant.principal_id,
            )
        )
    response = await service.client.post(f"{service.api}/browsers/attach", json={"name": "Existing", "cdp_url": URL})
    assert response.status_code == (201 if permitted else 403), response.text
    if permitted:
        browser = response.json()
        released = await service.client.post(
            f"{service.api}/browsers/{browser['id']}/release", headers=runs_kit.if_match(browser)
        )
        assert released.status_code == 403, released.text
    external_connect[1].assert_not_awaited()


async def test_external_use_honors_frozen_authority_and_workspace(service, runs_kit):
    from a13n_service.infra.errors import ServiceError
    from a13n_service.tenancy.authorize import ExecutionAuthority, Grant, Principal, WorkspaceScope

    browser = await attach(service, runs_kit)
    scope = WorkspaceScope(browser["organization_id"], browser["workspace_id"])
    actor = Principal(
        service.tenant.principal_id, "user", (Grant(scope.organization_id, None, frozenset({"read", "run", "write"})),)
    )
    ceiling = ExecutionAuthority(
        principal_id=actor.id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        verbs=frozenset({"read"}),
    )
    async with transaction(service.runtime.storage) as session:
        row = await lifecycle.find(session, scope.workspace_id, browser["id"])
        with pytest.raises(ServiceError, match="delegation"):
            await lifecycle.authorize_use(session, actor, row, scope, authority=ceiling)
        with pytest.raises(ServiceError) as missing:
            await lifecycle.find(session, "ws_another", browser["id"])
        assert missing.value.code == "not_found"


async def test_external_paid_call_refusal_precedes_connection(
    service, runs_kit, scripted_model, external_connect, invocation
):
    from a13n_service.infra.errors import ServiceError

    browser = await attach(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    invocation.tool_call.side_effect = ServiceError("forbidden", "Call refused", {})
    async with execution.open_backend(service.runtime, lease, binding, invocation, frozenset({"tabs"})) as backend:
        with pytest.raises(BrowserError):
            await backend.tabs("new")
    external_connect[1].assert_not_awaited()
    async with transaction(service.runtime.storage) as session:
        assert not await lifecycle.unsettled(session, browser["id"])


async def test_external_unsettled_operation_allows_terminal_release_after_run_exit(
    service, runs_kit, scripted_model, external_connect
):
    browser = await attach(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    from a13n_service.runs.attempts import AttemptControl
    from a13n_service.runs.execute import execute

    permit = await admission.admit(service.runtime, lease, binding)
    scripted_model.say("Done")
    await execute(service.runtime, lease, AttemptControl())
    assert (await runs_kit.get_run(service, lease.run_id))["status"] == "completed"
    current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
    response = await service.client.post(
        f"{service.api}/browsers/{browser['id']}/release", headers=runs_kit.if_match(current)
    )
    assert response.status_code == 200 and response.json()["status"] == "deleted", response.text
    async with transaction(service.runtime.storage) as session:
        from a13n_service.runs.browsers.tables import BrowserOperationRow

        assert (await session.get_one(BrowserOperationRow, permit.id)).settled_at is None
        assert (await session.get_one(BrowserRow, browser["id"])).handle is None
    external_connect[1].assert_not_awaited()


async def test_external_prepare_claim_refuses_release_and_converges(service, runs_kit, external_connect):
    import asyncio

    browser = await attach(service, runs_kit)
    supplier, connect = external_connect
    original = connect.side_effect
    entered, finish = asyncio.Event(), asyncio.Event()

    async def gated(*args, **kwargs):
        entered.set()
        await finish.wait()
        return await original(*args, **kwargs)

    connect.side_effect = gated
    preparing = asyncio.create_task(lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"]))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        await lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"])
        current = (await service.client.get(f"{service.api}/browsers/{browser['id']}")).json()
        response = await service.client.post(
            f"{service.api}/browsers/{browser['id']}/release", headers=runs_kit.if_match(current)
        )
        assert response.status_code == 409 and "browser_operation_pending" in response.text
        assert connect.await_count == 1
    finally:
        finish.set()
        await preparing
    assert (await command(service, runs_kit, browser["id"], "release"))["status"] == "deleted"
    assert supplier.pages == {"original"}


async def test_external_mcp_registration_and_release(service, runs_kit, external_connect):
    from .mcp_support import call, client, key

    await runs_kit.pause_sweeps(service)
    credential = await key(service)
    async with client(service, credential) as http:
        created = await call(
            http,
            service,
            "POST",
            "/browsers/attach",
            request_body={"name": "MCP existing", "cdp_url": URL, "headers": HEADERS},
        )
        assert created["status"] == 201, created
        browser = created["body"]
        assert browser["source"] == "external_cdp" and browser["provider_id"] is None
        assert "cdp_url" not in json.dumps(created) and "secret-url-token" not in json.dumps(created)
        released = await call(
            http,
            service,
            "POST",
            "/browsers/{browser_id}/release",
            browser_id=browser["id"],
            expected_version=created["headers"]["etag"],
        )
        assert released["status"] == 200 and released["body"]["status"] == "deleted"
    external_connect[1].assert_not_awaited()


async def test_ready_external_maintenance_does_not_probe(service, runs_kit, external_connect):
    _, connect = external_connect
    browser = await attach(service, runs_kit, prepare=True)
    connect.reset_mock()
    for _ in range(3):
        await maintenance.maintain(service.runtime, browser["workspace_id"], browser["id"])
    connect.assert_not_awaited()
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "ready" and row.claim_id is None
    await command(service, runs_kit, browser["id"], "prepare")
    connect.assert_awaited_once()


async def test_external_reconnect_retires_each_connection_once(
    service, runs_kit, scripted_model, external_connect, invocation
):
    supplier, _ = external_connect
    browser = await attach(service, runs_kit, prepare=True)
    supplier.connections.clear()
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    async with execution.open_backend(service.runtime, lease, binding, invocation, frozenset({"tabs"})) as backend:
        for _ in range(3):
            permit = await admission.admit(service.runtime, lease, binding)
            native = await backend._connect(permit)
            await admission.settle(service.runtime, permit)
            native.browser.disconnected = True
        permit = await admission.admit(service.runtime, lease, binding)
        await backend._connect(permit)
        await admission.settle(service.runtime, permit)
        assert len(supplier.connections) == 4
        for old in supplier.connections[:-1]:
            old.client.stop.assert_awaited_once()
        supplier.connections[-1].client.stop.assert_not_awaited()
    for connection in supplier.connections:
        connection.client.stop.assert_awaited_once()

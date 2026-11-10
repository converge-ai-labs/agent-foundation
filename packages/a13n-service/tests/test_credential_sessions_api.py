"""Application status is bounded, scoped evidence, never a connection or maintenance lease."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.infra import cursors
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.egress_credentials.notifications import CHANNEL
from a13n_service.resources.egress_credentials.service import request_sync
from a13n_service.runs.environments.egress.queries import credential_sessions
from a13n_service.runs.environments.lifecycle import LOST, Fault, advance, begin, record_failure
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.tables import AttemptRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal

from .environments_support import start
from .test_egress_sessions import prepared as prepared
from .test_egress_sessions import rotate
from .test_session_credentials import selection

pytestmark = pytest.mark.anyio


async def page(p, **params):
    path = f"{p.service.api}/egress-credentials/{p.credential['id']}/sessions"
    result = await p.service.client.get(path, params=params)
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    assert "private-token" not in result.text and "private-sentinel" not in result.text
    return result.json()


async def test_status_distinguishes_creation_pending_success_failure_and_idle(prepared):
    p = prepared
    initial = (await page(p))["items"][0]
    assert initial["status"] == "on_create" and initial["native_session_id"] is None
    assert p.device.opens == p.device.attaches == 0
    async with p.borrower.borrow(p.device, **p.options) as session:
        synced = (await page(p))["items"][0]
        assert synced["status"] == "applied" and synced["execution_active"]
        assert synced["applied_device_generation"] == "18446744073709551615"
        assert synced["applied_session_id"] == session.session_id
        await rotate(p, "next-private-token")
        pending = (await page(p))["items"][0]
        assert pending["status"] == "pending"
        assert pending["credential_version"] == 2 and pending["applied_credential_version"] == 1

        async def lost_response():
            raise TimeoutError("private-token must not enter a status response")

        session.after_update = lost_response
        assert await p.synchronizer.scan() == 0
        retry = (await page(p))["items"][0]
        assert retry["status"] == "retry" and retry["error_code"] == "credential_sync_timeout"
        assert retry["retry_at"] is not None
    # The Run no longer holds a live lease; queries must not mistake its recorded worker for an owner.
    async with transaction(p.service.runtime.storage) as db:
        attempt = await db.get_one(AttemptRow, p.lease.attempt_id)
        attempt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    calls = (p.device.opens, p.device.attaches, len(session.updates))
    idle = (await page(p))["items"][0]
    assert idle["status"] == "on_next_use" and not idle["execution_active"]
    assert idle["error_code"] is None and idle["retry_at"] is None
    environment_page = await p.service.client.get(
        f"{p.service.api}/environments/{p.mount.environment_id}/credential-sessions"
    )
    assert environment_page.status_code == 200, environment_page.text
    assert environment_page.json()["items"] == [idle]
    path = f"{p.service.api}/egress-credentials/{p.credential['id']}"
    current = await p.service.client.get(path)
    async with p.service.runtime.redis.pubsub() as notifications:
        await notifications.subscribe(CHANNEL)
        await notifications.get_message(timeout=1)
        requested = await p.service.client.post(f"{path}/sync")
        assert requested.status_code == 202, requested.text
        assert requested.json() == current.json() and requested.headers["etag"] == current.headers["etag"]
        message = await notifications.get_message(ignore_subscribe_messages=True, timeout=1)
        assert message is not None
        assert json.loads(message["data"]) == {"credential_id": p.credential["id"], "version": 2}
    assert await p.synchronizer.scan(force=True) == 0
    assert (p.device.opens, p.device.attaches, len(session.updates)) == calls


async def test_composite_pages_bind_cursor_to_target_and_validate_types(prepared):
    p = prepared
    accepted = await start(
        p.service,
        options={
            "environment": {
                "mode": "fresh",
                "template_id": p.service.template["id"],
                "session_configuration": selection(p.credential["id"]),
            }
        },
    )
    assert accepted["run"] is not None
    first = await page(p, limit=1)
    assert len(first["items"]) == 1 and first["next_cursor"] is not None
    second = await page(p, limit=1, cursor=first["next_cursor"])
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert first["items"][0]["thread_id"] != second["items"][0]["thread_id"]
    other = await p.service.client.get(
        f"{p.service.api}/environments/{p.mount.environment_id}/credential-sessions",
        params={"cursor": first["next_cursor"]},
    )
    assert other.status_code == 400 and other.json()["error"]["code"] == "invalid_cursor"
    owner = cursors.query_owner(p.service.tenant.workspace_id, p.credential["id"], None)
    for value in (True, "1", -1, 9223372036854775808):
        forged = cursors.encode(
            "session_credentials", owner, p.lease.thread_id, p.mount.environment_id, "default", value, "GH_TOKEN"
        )
        refused = await p.service.client.get(
            f"{p.service.api}/egress-credentials/{p.credential['id']}/sessions", params={"cursor": forged}
        )
        assert refused.status_code == 400, refused.text
    missing = await p.service.client.get(f"{p.service.api}/egress-credentials/ecred_missing/sessions")
    assert missing.status_code == 404
    assert p.device.opens == p.device.attaches == 0


async def test_query_scope_and_manual_retry_authority(prepared):
    p = prepared
    tenant = p.service.tenant
    runner = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["runner"]),)
    )
    visible = await credential_sessions(
        p.service.runtime.storage, runner, tenant.workspace_id, p.credential["id"], limit=10, cursor=None
    )
    assert len(visible.items) == 1
    with pytest.raises(ServiceError) as denied:
        await request_sync(
            p.service.runtime.storage, runner, tenant.workspace_id, p.credential["id"], redis=p.service.runtime.redis
        )
    assert denied.value.code == "forbidden"
    other = await p.service.client.post(f"{p.service.organization}/workspaces", json={"name": "Other"})
    assert other.status_code == 201, other.text
    headers = {"x-workspace-id": other.json()["id"]}
    for path in (
        f"{p.service.api}/egress-credentials/{p.credential['id']}/sessions",
        f"{p.service.api}/environments/{p.mount.environment_id}/credential-sessions",
    ):
        assert (await p.service.client.get(path, headers=headers)).status_code == 404
    assert (
        await p.service.client.post(f"{p.service.api}/egress-credentials/{p.credential['id']}/sync", headers=headers)
    ).status_code == 404


async def test_permanent_environment_failure_invalidates_application_status(prepared):
    p = prepared
    async with transaction(p.service.runtime.storage) as db:
        environment = await db.get_one(EnvironmentRow, p.mount.environment_id, with_for_update=True)
        await begin(db, environment, "creating")
    await advance(p.service.runtime, p.mount.environment_id, owner="credential-test")
    async with p.borrower.borrow(p.device, **p.options):
        assert (await page(p))["items"][0]["status"] == "applied"
        for fault, expected in (
            (Fault("provider_unavailable", "Temporary connection failure", "unknown"), "applied"),
            (LOST, "unknown"),
        ):
            async with transaction(p.service.runtime.storage) as db:
                environment = await db.get_one(EnvironmentRow, p.mount.environment_id)
                record_failure(environment, fault, datetime.now(UTC), unresolved=False)
            item = (await page(p))["items"][0]
            assert item["status"] == expected
            # Evidence is retained; an uncertain failure never claims native secrets were removed.
            assert item["applied_credential_version"] == 1 and item["applied_at"] is not None
        assert p.device.opens == 1 and p.device.attaches == 0

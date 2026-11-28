"""Signed connector acceptance and native admission against real PostgreSQL."""

import asyncio
import base64
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.providers.connector.composio.events import ComposioEvents
from a13n_harness.providers.connector.contracts import ConnectorProviderError
from a13n_harness.providers.connector.events import Event as SourceEvent
from a13n_harness.providers.connector.events import EventType, EventWebhook
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.connections.credentials import AccountSecret, protect
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.resources.connector_providers.webhooks import location, secret
from a13n_service.resources.providers.tables import ConnectorProviderRow
from a13n_service.runs.connector_events import dispatch, ingress, maintenance
from a13n_service.runs.connector_events.tables import EventRow
from a13n_service.runs.connector_events.triggers.operations import perform
from a13n_service.runs.connector_events.triggers.tables import TriggerRow
from a13n_service.runs.tables import InboxEntryRow, ThreadRow
from pydantic import SecretStr
from sqlalchemy import func, select

from .test_connections import composio_connection

pytestmark = pytest.mark.anyio
SECRET = "fixture-event-signing-secret"
DEFINITION = EventType(
    key="GITHUB_ISSUE",
    app="github",
    name="New issue",
    version="20261127_00",
    config_schema={"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]},
    payload_schema={"type": "object", "required": ["number"], "properties": {"number": {"type": "integer"}}},
)


def tag(row):
    return f'"{row["id"]}:{row["version"]}"'


@pytest.fixture
async def source(service, monkeypatch, runs_kit):
    await runs_kit.pause_sweeps(service)
    async with composio_connection(service, monkeypatch) as (_, connection):
        provider_id = connection["connector_provider_id"]
        async with transaction(service.runtime.storage) as db:
            row = await db.get_one(ConnectionRow, connection["id"])
            row.credential = protect(
                service.runtime.keys,
                row.organization_id,
                row.id,
                "credential",
                AccountSecret(account_ref="ca_fixture", correlation=row.id),
            )
            row.status = "ready"
            provider = await db.get_one(ConnectorProviderRow, provider_id)
            provider.event_webhook_id = "wh_fixture"
            provider.event_secret = service.runtime.keys.protect(SECRET.encode(), location(provider)).model_dump(
                mode="json"
            )
            provider.event_config = {
                "url": "https://events.example/api/v1/connector-webhooks/" + provider_id,
                "secret_version": 1,
            }
            await db.flush()
            provider_version = provider.version
        monkeypatch.setattr(ComposioEvents, "event_type", AsyncMock(return_value=DEFINITION))
        monkeypatch.setattr(ComposioEvents, "instances", AsyncMock(return_value=[]))
        monkeypatch.setattr(ComposioEvents, "upsert", AsyncMock(return_value="ti_fixture"))
        monkeypatch.setattr(
            ComposioEvents,
            "webhook",
            AsyncMock(
                return_value=EventWebhook(
                    id="wh_fixture",
                    url="https://events.example/api/v1/connector-webhooks/" + provider_id,
                    secret=SecretStr(SECRET),
                )
            ),
        )
        monkeypatch.setattr(ComposioEvents, "transition", AsyncMock())
        yield SimpleNamespace(
            service=service, provider_id=provider_id, provider_version=provider_version, connection_id=connection["id"]
        )


async def create(source, *, target=None, key="create"):
    service = source.service
    response = await service.client.post(
        service.api + "/connector-triggers",
        headers={"Idempotency-Key": key},
        json={
            "name": "Issues",
            "connection_id": source.connection_id,
            "event_type": DEFINITION.key,
            "parameters": {"repo": "example"},
            "target": target or {"mode": "store_only"},
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def event(source, source_id="msg_fixture", **changes):
    return SourceEvent.model_validate(
        {
            "source_id": source_id,
            "instance_id": "ti_fixture",
            "account_id": "ca_fixture",
            "user_id": source.connection_id,
            "event_type": DEFINITION.key,
            "occurred_at": "2026-11-27T00:00:00Z",
            "payload": {"number": 1},
            **changes,
        }
    )


async def receive(source, value=None):
    return await ingress.accept(
        source.service.runtime, source.provider_id, source.provider_version, value or event(source)
    )


async def new_thread_target(source, runs_kit, scripted_model):
    service = source.service
    agent = await runs_kit.create_agent(service, scripted_model)
    response = await service.client.post(service.workspace + "/service-accounts", json={"name": "Event runner"})
    assert response.status_code == 201, response.text
    return {"mode": "new_thread", "principal_id": response.json()["id"], "agent_id": agent["id"]}


async def test_duplicate_signed_acceptance_and_conflict(source):
    trigger = await create(source)
    await perform(source.service.runtime, trigger["id"])
    ids = await asyncio.gather(*[receive(source) for _ in range(8)])
    assert len(set(ids)) == 1
    with pytest.raises(ServiceError, match="source id reused"):
        await receive(source, event(source, payload={"number": 2}))
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(EventRow, ids[0])
        assert row.state == "stored" and row.payload == {"number": 1}
        assert await db.scalar(select(func.count()).select_from(EventRow)) == 1


@pytest.mark.parametrize("change", [{"account_id": "ca_other"}, {"user_id": "conn_other"}, {"event_type": "OTHER"}])
async def test_remote_identity_cannot_choose_local_authority(source, change):
    trigger = await create(source)
    await perform(source.service.runtime, trigger["id"])
    with pytest.raises(ServiceError, match="event source mismatch"):
        await receive(source, event(source, **change))
    async with short_session(source.service.runtime.storage) as db:
        assert await db.scalar(select(func.count()).select_from(EventRow)) == 0


async def test_concurrent_dispatch_ignores_trigger_pause_and_uses_snapshot(source, runs_kit, scripted_model):
    target = await new_thread_target(source, runs_kit, scripted_model)
    trigger = await create(source, target=target)
    assert trigger["target"]["agent_revision_id"]
    await perform(source.service.runtime, trigger["id"])
    event_id = await receive(source)
    path = source.service.api + "/connector-triggers/" + trigger["id"]
    current = await source.service.client.get(path)
    paused = await source.service.client.post(path + "/pause", headers={"If-Match": current.headers["etag"]})
    assert paused.status_code == 200, paused.text
    await asyncio.gather(*[dispatch.deliver(source.service.runtime, event_id) for _ in range(4)])
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(EventRow, event_id)
        assert row.state == "submitted", row.failure
        thread = await db.get_one(ThreadRow, row.thread_id)
        assert thread.app_id is None
        assert (
            await db.scalar(
                select(func.count()).select_from(InboxEntryRow).where(InboxEntryRow.thread_id == row.thread_id)
            )
            == 1
        )
    later = await receive(source, event(source, "msg_later"))
    incompatible = await receive(source, event(source, "msg_ignored_schema", payload={"number": "invalid"}))
    async with short_session(source.service.runtime.storage) as db:
        assert (await db.get_one(EventRow, later)).state == "ignored"
        assert (await db.get_one(EventRow, incompatible)).state == "ignored"
    assert await receive(source) == event_id


async def test_callback_before_create_response_uses_provisioning_target(source, runs_kit, scripted_model):
    target = await new_thread_target(source, runs_kit, scripted_model)
    trigger = await create(source, target=target)
    event_id = await receive(source)
    await perform(source.service.runtime, trigger["id"])
    path = source.service.api + "/connector-triggers/" + trigger["id"]
    current = await source.service.client.get(path)
    changed = await source.service.client.patch(
        path, headers={"If-Match": current.headers["etag"]}, json={"target": {"mode": "store_only"}}
    )
    assert changed.status_code == 200, changed.text
    await maintenance.associate(source.service.runtime)
    await dispatch.deliver(source.service.runtime, event_id)
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(EventRow, event_id)
        assert row.state == "submitted", row.failure
        assert row.snapshot["target"]["principal_id"] == target["principal_id"]


async def test_revoked_service_account_cannot_use_saved_authority(source, runs_kit, scripted_model):
    target = await new_thread_target(source, runs_kit, scripted_model)
    trigger = await create(source, target=target)
    await perform(source.service.runtime, trigger["id"])
    event_id = await receive(source)
    path = source.service.workspace + "/service-accounts/" + target["principal_id"]
    current = await source.service.client.get(path)
    response = await source.service.client.patch(
        path, json={"status": "disabled"}, headers={"If-Match": current.headers["etag"]}
    )
    assert response.status_code == 200, response.text
    await dispatch.deliver(source.service.runtime, event_id)
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(EventRow, event_id)
        assert row.state == "failed" and row.inbox_id is None


async def test_inbox_failure_rolls_back_thread_and_event_receipt(source, runs_kit, scripted_model, monkeypatch):
    from a13n_service.runs import submit

    target = await new_thread_target(source, runs_kit, scripted_model)
    trigger = await create(source, target=target)
    await perform(source.service.runtime, trigger["id"])
    event_id = await receive(source)
    original = submit.create_thread_in_transaction

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("commit interrupted")

    monkeypatch.setattr(submit, "create_thread_in_transaction", fail)
    await dispatch.deliver(source.service.runtime, event_id)
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(EventRow, event_id)
        assert row.state == "pending" and row.inbox_id is None
        assert await db.scalar(select(func.count()).select_from(ThreadRow)) == 0
    monkeypatch.setattr(submit, "create_thread_in_transaction", original)
    async with transaction(source.service.runtime.storage) as db:
        row = await db.get_one(EventRow, event_id)
        row.due_at = await now(db)
    await dispatch.deliver(source.service.runtime, event_id)
    async with short_session(source.service.runtime.storage) as db:
        assert (await db.get_one(EventRow, event_id)).state == "submitted"


@pytest.mark.parametrize("credential", ["unrelated", "run"])
async def test_http_signature_rejects_tampering_stale_timestamp_and_bearer_confusion(
    source, scripted_model, runs_kit, credential
):
    trigger = await create(source)
    await perform(source.service.runtime, trigger["id"])
    payload = {
        "id": "msg_fixture",
        "type": "composio.trigger.message",
        "timestamp": "2026-11-27T00:00:00Z",
        "metadata": {
            "trigger_id": "ti_fixture",
            "trigger_slug": DEFINITION.key,
            "connected_account_id": "ca_fixture",
            "user_id": source.connection_id,
        },
        "data": {"number": 1},
    }
    raw = json.dumps(payload).encode()
    timestamp = str(int(datetime.now(UTC).timestamp()))
    signature = base64.b64encode(
        hmac.digest(SECRET.encode(), b"msg_fixture." + timestamp.encode() + b"." + raw, "sha256")
    ).decode()
    token = "this-is-not-a-service-key"
    if credential == "run":
        from a13n_service.infra.platform import PLATFORM_GROUPS
        from a13n_service.runs.platform_tokens import issue

        from .test_platform_tools import lease_for

        lease, _, _ = await lease_for(source.service, scripted_model, runs_kit)
        token = await issue(source.service.runtime, lease, frozenset(PLATFORM_GROUPS))
    headers = {
        "webhook-id": "msg_fixture",
        "webhook-timestamp": timestamp,
        "webhook-signature": "v1," + signature,
        "Authorization": "Bearer " + token,
    }
    path = source.service.api + "/connector-webhooks/" + source.provider_id
    unsigned = await source.service.client.post(path, content=raw, headers={"Authorization": "Bearer " + token})
    assert unsigned.status_code == 401, unsigned.text
    async with short_session(source.service.runtime.storage) as db:
        assert await db.scalar(select(func.count()).select_from(EventRow)) == 0
    result = await source.service.client.post(path, content=raw, headers=headers)
    assert result.status_code == 204, result.text
    assert (await source.service.client.post(path, content=raw + b" ", headers=headers)).status_code == 401
    assert (
        await source.service.client.post(path, content=raw, headers={**headers, "webhook-timestamp": "1"})
    ).status_code == 401
    assert (await source.service.client.get(path, headers=headers)).status_code == (403 if credential == "run" else 401)


async def test_payload_failure_is_saved_and_not_submitted(source):
    trigger = await create(source)
    await perform(source.service.runtime, trigger["id"])
    event_id = await receive(source, event(source, payload={"number": "wrong"}))
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(EventRow, event_id)
        assert row.state == "failed" and row.failure == "event_payload_schema_mismatch"


async def test_retention_keeps_deduplication_after_payload_expiry(source, monkeypatch):
    trigger = await create(source)
    await perform(source.service.runtime, trigger["id"])
    event_id = await receive(source)
    monkeypatch.setattr(maintenance, "now", AsyncMock(return_value=datetime.now(UTC) + timedelta(days=8)))
    await maintenance.cleanup(source.service.runtime)
    assert await receive(source) == event_id
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(EventRow, event_id)
        assert row.payload is None and row.digest


async def test_create_replay_and_duplicate_subscription(source):
    first = await create(source)
    assert (await create(source))["id"] == first["id"]
    response = await source.service.client.post(
        source.service.api + "/connector-triggers",
        headers={"Idempotency-Key": "second"},
        json={
            "name": "Other",
            "connection_id": source.connection_id,
            "event_type": DEFINITION.key,
            "parameters": {"repo": "example"},
        },
    )
    assert response.status_code == 409, response.text


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ConnectorProviderError("provider_unavailable", retryable=True, outcome_unknown=True, http_status=503),
        ValueError("invalid creation response"),
    ],
)
async def test_unknown_upstream_creation_cannot_adopt_a_matching_subscription(source, monkeypatch, error):
    from a13n_harness.providers.connector.events import EventInstance

    trigger = await create(source)
    unknown = AsyncMock(side_effect=error)
    monkeypatch.setattr(ComposioEvents, "upsert", unknown)
    await perform(source.service.runtime, trigger["id"])
    monkeypatch.setattr(
        ComposioEvents,
        "instances",
        AsyncMock(
            return_value=[
                EventInstance(
                    id="ti_fixture",
                    account_id="ca_fixture",
                    user_id=source.connection_id,
                    event_type=DEFINITION.key,
                    config={"repo": "example"},
                    disabled=False,
                    version=DEFINITION.version,
                )
            ]
        ),
    )
    async with transaction(source.service.runtime.storage) as db:
        row = await db.get_one(TriggerRow, trigger["id"])
        row.operation_due_at = await now(db)
    await perform(source.service.runtime, trigger["id"])
    assert unknown.await_count == 1
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(TriggerRow, trigger["id"])
        assert row.remote_id is None and row.failure == "subscription_creation_unconfirmed"
    ComposioEvents.transition.assert_not_awaited()
    path = source.service.api + "/connector-triggers/" + trigger["id"]
    current = await source.service.client.get(path)
    assert not current.json()["retryable"]
    retry = await source.service.client.post(path + "/reconcile", headers={"If-Match": current.headers["etag"]})
    assert retry.status_code == 409
    retired = await source.service.client.post(path + "/retire", headers={"If-Match": current.headers["etag"]})
    assert retired.status_code == 200
    await perform(source.service.runtime, trigger["id"])
    ComposioEvents.transition.assert_not_awaited()


async def test_pause_during_create_preserves_remote_cleanup_and_accepted_event(source, monkeypatch):
    trigger = await create(source)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def upsert(*args):
        entered.set()
        await finish.wait()
        return "ti_fixture"

    monkeypatch.setattr(ComposioEvents, "upsert", upsert)
    task = asyncio.create_task(perform(source.service.runtime, trigger["id"]))
    await entered.wait()
    path = source.service.api + "/connector-triggers/" + trigger["id"]
    current = await source.service.client.get(path)
    response = await source.service.client.post(path + "/pause", headers={"If-Match": current.headers["etag"]})
    assert response.status_code == 200, response.text
    finish.set()
    await task
    await perform(source.service.runtime, trigger["id"])
    ComposioEvents.transition.assert_awaited_once_with("ti_fixture", "paused")
    current = (await source.service.client.get(path)).json()
    assert current["state"] == "paused" and current["operation_kind"] is None


async def test_api_key_rotation_proves_same_webhook_and_preserves_signing_key(source, monkeypatch):
    path = source.service.api + "/connector-providers/" + source.provider_id
    before = await source.service.client.get(path)
    result = await source.service.client.patch(
        path, headers={"If-Match": before.headers["etag"]}, json={"credential": {"api_key": "rotated-project-key"}}
    )
    assert result.status_code == 200, result.text
    async with short_session(source.service.runtime.storage) as db:
        provider = await db.get_one(ConnectorProviderRow, source.provider_id)
        assert secret(source.service.runtime, provider) == SECRET
    monkeypatch.setattr(
        ComposioEvents,
        "webhook",
        AsyncMock(
            return_value=EventWebhook(id="wh_other_project", url="https://events.example", secret=SecretStr("other"))
        ),
    )
    rejected = await source.service.client.patch(
        path, headers={"If-Match": result.headers["etag"]}, json={"credential": {"api_key": "other-project-key"}}
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["details"]["reason"] == "event_project_changed"


async def test_remote_retry_is_bounded_and_manually_recoverable(source, monkeypatch):
    trigger = await create(source)
    monkeypatch.setattr(ComposioEvents, "instances", AsyncMock(side_effect=TimeoutError))
    limit = source.service.runtime.settings.connector_events.max_attempts
    for _ in range(limit + 1):
        async with transaction(source.service.runtime.storage) as db:
            row = await db.get_one(TriggerRow, trigger["id"])
            row.operation_due_at = await now(db)
        await perform(source.service.runtime, trigger["id"])
    assert ComposioEvents.instances.await_count == limit
    ComposioEvents.upsert.assert_not_awaited()
    path = source.service.api + "/connector-triggers/" + trigger["id"]
    current = await source.service.client.get(path)
    retry = await source.service.client.post(path + "/reconcile", headers={"If-Match": current.headers["etag"]})
    assert retry.status_code == 200, retry.text
    assert retry.json()["operation_kind"] == "create"


async def test_event_immutability_and_payload_cleanup(source):
    from sqlalchemy.exc import DBAPIError

    trigger = await create(source)
    await perform(source.service.runtime, trigger["id"])
    event_id = await receive(source)
    for changes in (
        {"payload": {"number": 2}},
        {"source_id": "forged"},
        {"snapshot": {"target": {"mode": "new_thread"}}},
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            async with transaction(source.service.runtime.storage) as db:
                row = await db.get_one(EventRow, event_id)
                for key, value in changes.items():
                    setattr(row, key, value)
                await db.flush()


async def test_etag_required_and_event_body_bound(source):
    trigger = await create(source)
    path = source.service.api + "/connector-triggers/" + trigger["id"]
    response = await source.service.client.post(path + "/pause")
    assert response.status_code == 428, response.text
    response = await source.service.client.patch(path, headers={"If-Match": '"stale:1"'}, json={"name": "changed"})
    assert response.status_code == 412, response.text
    response = await source.service.client.post(
        source.service.api + "/connector-webhooks/" + source.provider_id,
        content=b"x" * (source.service.runtime.settings.connector_events.body_bytes + 1),
    )
    assert response.status_code == 413, response.text


async def test_disconnected_account_pauses_subscription(source):
    from a13n_service.runs.connector_events.triggers.operations import check_bindings

    trigger = await create(source)
    await perform(source.service.runtime, trigger["id"])
    async with transaction(source.service.runtime.storage) as db:
        row = await db.get_one(ConnectionRow, source.connection_id)
        row.credential = None
        row.status = "pending"
    await check_bindings(source.service.runtime)
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(TriggerRow, trigger["id"])
        assert row.state == "paused" and row.failure == "connection_changed"


async def test_management_scope_and_unmatched_visibility(source):
    from a13n_service.runs.connector_events import service as history
    from a13n_service.runs.connector_events.triggers import service as triggers
    from a13n_service.runs.connector_events.triggers.schemas import TriggerUpdate
    from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal

    service = source.service
    trigger = await create(source)
    viewer = Principal(
        service.tenant.principal_id,
        "user",
        (Grant(service.tenant.organization_id, service.tenant.workspace_id, BUILT_IN_ROLES["viewer"]),),
    )
    assert (await triggers.get(service.runtime, viewer, service.tenant.workspace_id, trigger["id"])).id == trigger["id"]
    with pytest.raises(ServiceError) as denied:
        await triggers.update(
            service.runtime,
            viewer,
            service.tenant.workspace_id,
            trigger["id"],
            TriggerUpdate(name="changed"),
            if_match=tag(trigger),
        )
    assert denied.value.code == "forbidden"
    event_id = await receive(source)
    with pytest.raises(ServiceError) as hidden:
        await history.get(service.runtime, viewer, service.tenant.workspace_id, event_id)
    assert hidden.value.code == "forbidden"
    listing = await history.list_events(
        service.runtime, viewer, service.tenant.workspace_id, trigger_id=None, cursor=None, limit=10
    )
    assert listing.items == []


async def test_backlog_capacity_and_unmatched_deadline(source, monkeypatch):
    original = source.service.runtime
    config = original.settings.connector_events.model_copy(update={"unmatched_per_provider": 1})
    runtime = replace(original, settings=original.settings.model_copy(update={"connector_events": config}))
    event_id = await ingress.accept(runtime, source.provider_id, source.provider_version, event(source))
    with pytest.raises(ServiceError) as full:
        await ingress.accept(runtime, source.provider_id, source.provider_version, event(source, "msg_second"))
    assert full.value.code == "rate_limited"
    monkeypatch.setattr(maintenance, "now", AsyncMock(return_value=datetime.now(UTC) + timedelta(minutes=10)))
    await maintenance.associate(source.service.runtime)
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(EventRow, event_id)
        assert row.state == "failed" and row.failure == "trigger_mapping_unavailable"


async def test_rotation_timeout_recovers_current_key_without_second_rotation(source, monkeypatch):
    from a13n_service.resources.connector_providers import webhooks

    from .test_connections import admin

    runtime = source.service.runtime
    config = runtime.settings.connector_events.model_copy(update={"public_url": "https://events.example"})
    runtime = replace(runtime, settings=runtime.settings.model_copy(update={"connector_events": config}))
    remote = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(ComposioEvents, "configure_webhook", remote)
    with pytest.raises(ServiceError) as failure:
        await webhooks.configure(
            runtime,
            admin(source.service),
            source.service.tenant.workspace_id,
            source.provider_id,
            if_match=f'"{source.provider_id}:{source.provider_version}"',
            rotate=True,
        )
    assert failure.value.code == "unavailable"
    async with transaction(runtime.storage) as db:
        provider = await db.get_one(ConnectorProviderRow, source.provider_id)
        provider.event_operation = {**provider.event_operation, "deadline": datetime.now(UTC).isoformat()}
    recovered = AsyncMock(
        return_value=EventWebhook(
            id="wh_fixture",
            url="https://events.example/api/v1/connector-webhooks/" + source.provider_id,
            secret=SecretStr("rotated-after-timeout"),
        )
    )
    monkeypatch.setattr(ComposioEvents, "configure_webhook", recovered)
    await webhooks.recover(runtime)
    assert recovered.await_args.kwargs == {"rotate": False}
    async with short_session(runtime.storage) as db:
        provider = await db.get_one(ConnectorProviderRow, source.provider_id)
        assert provider.event_operation is None
        assert secret(runtime, provider) == "rotated-after-timeout"


async def test_history_paginates_newest_first_and_binds_filter(source):
    trigger = await create(source)
    await perform(source.service.runtime, trigger["id"])
    first = await receive(source, event(source, source_id="msg_first"))
    second = await receive(source, event(source, source_id="msg_second"))
    client, api = source.service.client, source.service.api
    response = await client.get(api + "/connector-events", params={"limit": 1, "trigger_id": trigger["id"]})
    assert response.status_code == 200, response.text
    page = response.json()
    assert [item["id"] for item in page["items"]] == [second]
    response = await client.get(
        api + "/connector-events", params={"limit": 1, "trigger_id": trigger["id"], "cursor": page["next_cursor"]}
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [first]
    assert response.json()["next_cursor"] is None
    response = await client.get(api + "/connector-events", params={"cursor": page["next_cursor"]})
    assert response.status_code == 400


@pytest.mark.parametrize("manual", [False, True])
async def test_query_failure_never_authorizes_adopting_an_existing_subscription(source, monkeypatch, manual):
    from a13n_harness.providers.connector.events import EventInstance

    trigger = await create(source)
    remote = EventInstance(
        id="ti_owned_elsewhere",
        account_id="ca_fixture",
        user_id=source.connection_id,
        event_type=DEFINITION.key,
        config={"repo": "example"},
        disabled=True,
        version=DEFINITION.version,
    )
    monkeypatch.setattr(ComposioEvents, "instances", AsyncMock(side_effect=[TimeoutError(), [remote]]))
    await perform(source.service.runtime, trigger["id"])
    path = source.service.api + "/connector-triggers/" + trigger["id"]
    current = await source.service.client.get(path)
    if manual:
        response = await source.service.client.post(path + "/reconcile", headers={"If-Match": current.headers["etag"]})
        assert response.status_code == 200, response.text
        assert response.json()["operation_kind"] == "create"
    else:
        async with transaction(source.service.runtime.storage) as db:
            row = await db.get_one(TriggerRow, trigger["id"])
            row.operation_due_at = await now(db)
    await perform(source.service.runtime, trigger["id"])
    current = await source.service.client.get(path)
    assert current.json()["remote_id"] is None
    assert current.json()["failure"] == "subscription_owned_elsewhere"
    assert not current.json()["retryable"]
    response = await source.service.client.post(path + "/retire", headers={"If-Match": current.headers["etag"]})
    assert response.status_code == 200, response.text
    await perform(source.service.runtime, trigger["id"])
    ComposioEvents.upsert.assert_not_awaited()
    ComposioEvents.transition.assert_not_awaited()


@pytest.mark.parametrize("command,reenable", [("pause", False), ("retire", False), ("pause", True)])
async def test_creation_callbacks_respect_local_stop_time(
    source, monkeypatch, runs_kit, scripted_model, command, reenable
):
    target = await new_thread_target(source, runs_kit, scripted_model)
    trigger = await create(source, target=target)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def upsert(*args):
        entered.set()
        await finish.wait()
        return "ti_fixture"

    monkeypatch.setattr(ComposioEvents, "upsert", upsert)
    runtime = source.service.runtime
    task = asyncio.create_task(perform(runtime, trigger["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        before = await receive(source, event(source, "msg_before_stop"))
        path = source.service.api + "/connector-triggers/" + trigger["id"]
        current = await source.service.client.get(path)
        stopped = await source.service.client.post(path + "/" + command, headers={"If-Match": current.headers["etag"]})
        assert stopped.status_code == 200, stopped.text
        after = await receive(source, event(source, "msg_after_stop"))
    finally:
        finish.set()
        await task
    await perform(runtime, trigger["id"])
    if reenable:
        current = await source.service.client.get(path)
        enabled = await source.service.client.post(path + "/enable", headers={"If-Match": current.headers["etag"]})
        assert enabled.status_code == 200, enabled.text
        await perform(runtime, trigger["id"])
    await maintenance.associate(runtime)
    await dispatch.deliver(runtime, before)
    await dispatch.deliver(runtime, after)
    async with short_session(runtime.storage) as db:
        accepted = await db.get_one(EventRow, before)
        ignored = await db.get_one(EventRow, after)
        row = await db.get_one(TriggerRow, trigger["id"])
        assert accepted.created_at < row.provisioning_stopped_at <= ignored.created_at
        assert accepted.state == "submitted" and accepted.thread_id is not None
        assert ignored.state == "ignored" and ignored.thread_id is None


@pytest.mark.parametrize("sweep_first", [False, True])
async def test_cancelled_final_attempt_is_visible_and_recoverable(source, monkeypatch, sweep_first):
    from a13n_service.runs.connector_events.triggers.operations import maintain

    trigger = await create(source)
    runtime = source.service.runtime
    await perform(runtime, trigger["id"])
    path = source.service.api + "/connector-triggers/" + trigger["id"]
    current = await source.service.client.get(path)
    response = await source.service.client.post(path + "/pause", headers={"If-Match": current.headers["etag"]})
    assert response.status_code == 200
    async with transaction(runtime.storage) as db:
        row = await db.get_one(TriggerRow, trigger["id"])
        row.operation_attempts = runtime.settings.connector_events.max_attempts - 1
        row.failure = "old_failure"
    monkeypatch.setattr(ComposioEvents, "transition", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await perform(runtime, trigger["id"])
    current = await source.service.client.get(path)
    assert current.json()["failure"] is None and not current.json()["retryable"]
    blocked = await source.service.client.post(path + "/reconcile", headers={"If-Match": current.headers["etag"]})
    assert blocked.status_code == 409
    async with transaction(runtime.storage) as db:
        row = await db.get_one(TriggerRow, trigger["id"])
        row.operation_due_at = await now(db)
    if sweep_first:
        await maintain(runtime)
    current = await source.service.client.get(path)
    assert current.json()["retryable"]
    if sweep_first:
        assert current.json()["failure"] == "upstream_operation_unconfirmed"
    response = await source.service.client.post(path + "/reconcile", headers={"If-Match": current.headers["etag"]})
    assert response.status_code == 200, response.text
    monkeypatch.setattr(ComposioEvents, "transition", AsyncMock())
    await perform(runtime, trigger["id"])
    current = await source.service.client.get(path)
    assert current.json()["operation_kind"] is None
    ComposioEvents.transition.assert_awaited_once_with("ti_fixture", "paused")


async def test_interrupted_creation_becomes_unconfirmed_without_reissuing_request(source, monkeypatch):
    from a13n_service.runs.connector_events.triggers.operations import maintain

    trigger = await create(source)
    runtime = source.service.runtime
    monkeypatch.setattr(ComposioEvents, "upsert", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await perform(runtime, trigger["id"])
    async with transaction(runtime.storage) as db:
        row = await db.get_one(TriggerRow, trigger["id"])
        assert row.operation_kind == "create_unconfirmed" and row.failure is None
        row.operation_due_at = await now(db)
    await maintain(runtime)
    await maintain(runtime)
    current = await source.service.client.get(source.service.api + "/connector-triggers/" + trigger["id"])
    assert current.json()["failure"] == "subscription_creation_unconfirmed"
    assert current.json()["remote_id"] is None and not current.json()["retryable"]
    assert ComposioEvents.upsert.await_count == 1
    ComposioEvents.transition.assert_not_awaited()


async def test_webhook_final_attempt_must_expire_before_manual_recovery(source, monkeypatch):
    from a13n_service.resources.connector_providers import webhooks

    from .test_connections import admin

    original = source.service.runtime
    config = original.settings.connector_events.model_copy(
        update={"public_url": "https://events.example", "max_attempts": 1}
    )
    runtime = replace(original, settings=original.settings.model_copy(update={"connector_events": config}))
    actor = admin(source.service)
    workspace_id = source.service.tenant.workspace_id
    remote = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(ComposioEvents, "configure_webhook", remote)
    with pytest.raises(asyncio.CancelledError):
        await webhooks.configure(
            runtime,
            actor,
            workspace_id,
            source.provider_id,
            if_match=f'"{source.provider_id}:{source.provider_version}"',
            rotate=True,
        )
    current = await webhooks.get(runtime, actor, workspace_id, source.provider_id)
    assert not current.retryable
    with pytest.raises(ServiceError, match="operation in progress"):
        await webhooks.configure(
            runtime, actor, workspace_id, source.provider_id, if_match=f'"{source.provider_id}:{current.version}"'
        )
    async with transaction(runtime.storage) as db:
        row = await db.get_one(ConnectorProviderRow, source.provider_id)
        row.event_operation = {**row.event_operation, "deadline": (await now(db)).isoformat()}
    await webhooks.recover(runtime)
    current = await webhooks.get(runtime, actor, workspace_id, source.provider_id)
    assert current.retryable and current.failure == "upstream_operation_unconfirmed"
    remote.side_effect = None
    remote.return_value = EventWebhook(id="wh_fixture", url=current.url, secret=SecretStr(SECRET))
    recovered = await webhooks.configure(
        runtime,
        actor,
        workspace_id,
        source.provider_id,
        if_match=f'"{source.provider_id}:{current.version}"',
        rotate=True,
    )
    assert recovered.failure is None and recovered.operation is None
    assert remote.await_args.kwargs == {"rotate": False}


@pytest.mark.parametrize("retryable,manual", [(True, False), (True, True), (False, True)])
async def test_definite_creation_rejection_preserves_safe_recovery(source, monkeypatch, retryable, manual):
    runtime = source.service.runtime
    trigger = await create(source)
    rejected = ConnectorProviderError(
        "rate_limited" if retryable else "provider_rejected",
        retryable=retryable,
        outcome_unknown=False,
        retry_after_seconds=30 if retryable else None,
        http_status=429 if retryable else 400,
    )
    upsert = AsyncMock(side_effect=[rejected, "ti_fixture"])
    monkeypatch.setattr(ComposioEvents, "upsert", upsert)
    await perform(runtime, trigger["id"])
    path = source.service.api + "/connector-triggers/" + trigger["id"]
    current = await source.service.client.get(path)
    assert current.json()["failure"] == rejected.code
    assert current.json()["operation_kind"] == "create" and current.json()["retryable"]
    async with short_session(runtime.storage) as db:
        row = await db.get_one(TriggerRow, trigger["id"])
        assert row.remote_id is None
        if retryable:
            assert row.operation_attempts == 1
            assert row.operation_due_at >= row.updated_at + timedelta(seconds=29)
        else:
            assert row.operation_attempts == runtime.settings.connector_events.max_attempts
            assert row.operation_due_at is None
    await perform(runtime, trigger["id"])
    assert upsert.await_count == 1
    if manual:
        recovered = await source.service.client.post(path + "/reconcile", headers={"If-Match": current.headers["etag"]})
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["operation_kind"] == "create"
    else:
        async with transaction(runtime.storage) as db:
            row = await db.get_one(TriggerRow, trigger["id"])
            row.operation_due_at = await now(db)
    await perform(runtime, trigger["id"])
    current = await source.service.client.get(path)
    assert current.json()["remote_id"] == "ti_fixture" and current.json()["operation_kind"] is None
    assert upsert.await_count == 2 and ComposioEvents.instances.await_count == 2


async def test_creation_rejection_retry_still_checks_for_another_owner(source, monkeypatch):
    from a13n_harness.providers.connector.events import EventInstance

    trigger = await create(source)
    runtime = source.service.runtime
    monkeypatch.setattr(
        ComposioEvents, "upsert", AsyncMock(side_effect=ConnectorProviderError("rate_limited", retryable=True))
    )
    await perform(runtime, trigger["id"])
    monkeypatch.setattr(
        ComposioEvents,
        "instances",
        AsyncMock(
            return_value=[
                EventInstance(
                    id="ti_other_owner",
                    account_id="ca_fixture",
                    user_id=source.connection_id,
                    event_type=DEFINITION.key,
                    config={"repo": "example"},
                    disabled=True,
                    version=DEFINITION.version,
                )
            ]
        ),
    )
    async with transaction(runtime.storage) as db:
        row = await db.get_one(TriggerRow, trigger["id"])
        row.operation_due_at = await now(db)
    await perform(runtime, trigger["id"])
    async with short_session(runtime.storage) as db:
        row = await db.get_one(TriggerRow, trigger["id"])
        assert row.remote_id is None and row.failure == "subscription_owned_elsewhere"
    assert ComposioEvents.upsert.await_count == 1
    ComposioEvents.transition.assert_not_awaited()


async def test_error_after_acknowledged_creation_cannot_make_it_repeatable(source, monkeypatch):
    from contextlib import asynccontextmanager

    from a13n_service.runs.connector_events.triggers import operations

    original = operations.open_events

    @asynccontextmanager
    async def fail_on_close(*args):
        async with original(*args) as adapter:
            yield adapter
        raise ConnectorProviderError("rate_limited", retryable=True, outcome_unknown=False)

    monkeypatch.setattr(operations, "open_events", fail_on_close)
    trigger = await create(source)
    await perform(source.service.runtime, trigger["id"])
    async with short_session(source.service.runtime.storage) as db:
        row = await db.get_one(TriggerRow, trigger["id"])
        assert row.operation_kind == "create_unconfirmed"
        assert row.failure == "subscription_creation_unconfirmed" and row.operation_due_at is None
    assert ComposioEvents.upsert.await_count == 1

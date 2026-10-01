"""Subscriptions: admin configuration, write-only signing secrets, signed webhook delivery and redelivery."""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import Delivery, OutboxRow, Policy, enqueue
from a13n_service.resources.subscriptions import service as subscriptions
from a13n_service.resources.subscriptions.delivery import (
    RunFacts,
    WebhookSender,
    signature,
    stage_webhooks,
    webhook_target,
)
from a13n_service.resources.subscriptions.schemas import SECRET_UNAVAILABLE, SubscriptionCreate
from a13n_service.resources.subscriptions.tables import SubscriptionRow
from a13n_service.runs.tables import RunRow
from a13n_service.runs.webhooks import notify_subscribers
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from a13n_service.tenancy.tables import GrantRow, PrincipalRow
from sqlalchemy import select

pytestmark = pytest.mark.anyio

SECRET = "whsec_test_signing_secret_value"
THREAD = "thread_" + "0" * 32


def etag(resource: dict) -> str:
    return f'"{resource["id"]}:{resource["version"]}"'


async def test_subscription_configuration_is_admin_only(service) -> None:  # type: ignore[no-untyped-def]
    base = f"{service.api}/subscriptions"
    body = {"name": "Runs", "url": "http://127.0.0.1:9/hook", "kinds": ["run.completed", "run.completed"]}
    created = await service.client.post(base, json=body)
    assert created.status_code == 201, created.text
    subscription = created.json()
    assert subscription["signing_secret"].startswith("whsec_") and subscription["kinds"] == ["run.completed"]
    supplied = await service.client.post(
        base, json={**body, "name": "Supplied", "signing_secret": SECRET, "filter": {"thread_id": THREAD}}
    )
    assert supplied.json()["signing_secret"] == SECRET and supplied.json()["filter"]["thread_id"] == THREAD

    item = f"{base}/{subscription['id']}"
    fetched = await service.client.get(item)
    assert "signing_secret" not in fetched.json() and fetched.headers["etag"] == etag(subscription)
    listed = (await service.client.get(base)).json()["items"]
    assert len(listed) == 2 and all("signing_secret" not in entry for entry in listed)
    async with short_session(service.runtime.storage) as session:
        stored = await session.get_one(SubscriptionRow, supplied.json()["id"])
    assert SECRET not in json.dumps(stored.signing_secret)

    assert (await service.client.post(base, json={**body, "kinds": ["run.started"]})).status_code == 400
    # URL fragments are not part of an HTTP destination.
    invalid = await service.client.post(base, json={**body, "url": "http://127.0.0.1:9/hook#fragment"})
    assert invalid.status_code == 400 and invalid.json()["error"]["details"]["field"] == "url"

    change = {"kinds": ["run.failed", "run_attempt.failed"], "enabled": False}
    assert (await service.client.patch(item, json=change)).status_code == 428
    updated = await service.client.patch(item, json=change, headers={"if-match": etag(subscription)})
    assert updated.status_code == 200 and updated.json()["kinds"] == change["kinds"]
    assert updated.json()["enabled"] is False
    # A change to nothing keeps the version and records no event.
    unchanged = await service.client.patch(item, json=change, headers={"if-match": updated.headers["etag"]})
    assert unchanged.status_code == 200 and unchanged.json()["version"] == updated.json()["version"]
    async with short_session(service.runtime.storage) as session:
        events = (
            await session.scalars(select(AuditEventRow).where(AuditEventRow.target_id == subscription["id"]))
        ).all()
    assert sorted((event.action, event.details) for event in events) == [
        ("subscription.create", {}),
        ("subscription.update", {"fields": ["kinds", "enabled"]}),
    ]
    deleted = await service.client.delete(item, headers={"if-match": updated.headers["etag"]})
    assert deleted.status_code == 204 and (await service.client.get(item)).status_code == 404

    runtime, tenant = service.runtime, service.tenant
    admin = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["admin"]),)
    )
    request = SubscriptionCreate.model_validate(body)
    with pytest.raises(ServiceError) as limited:
        await subscriptions.create_subscription(
            runtime.storage,
            runtime.access,
            runtime.keys,
            runtime.endpoint_policy,
            admin,
            tenant.workspace_id,
            request,
            limit=1,
        )
    assert limited.value.details["reason"] == "subscription_limit"

    # Authority is re-read inside the transaction, so the builder is a stored principal with a stored grant.
    builder_id = new_object_id("usr")
    async with transaction(runtime.storage) as session:
        session.add(PrincipalRow(id=builder_id, kind="user", name="builder", email="builder@example.com"))
        await session.flush()
        session.add(
            GrantRow(
                id=new_object_id("rb"),
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                principal_id=builder_id,
                role="builder",
                created_by_id=tenant.principal_id,
            )
        )
    builder = Principal(
        builder_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["builder"]),)
    )
    with pytest.raises(ServiceError) as denied:
        await subscriptions.list_subscriptions(
            runtime.storage, runtime.access, builder, tenant.workspace_id, limit=10, cursor=None
        )
    assert denied.value.code == "forbidden"
    async with short_session(runtime.storage) as session:
        denial = await session.scalar(select(AuditEventRow).where(AuditEventRow.outcome == "denied"))
    assert denial is not None and denial.action == "subscription.read"


@contextmanager
def receiver(
    statuses: list[int], *, headers: dict[str, str] | None = None, body: bytes = b""
) -> Iterator[tuple[str, list[tuple[dict[str, str], bytes]]]]:
    """A local webhook endpoint answering with `statuses` in order, each with `headers` and `body`, and
    recording each request."""
    received: list[tuple[dict[str, str], bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            sent = self.rfile.read(int(self.headers["content-length"]))
            received.append(({key.lower(): value for key, value in self.headers.items()}, sent))
            self.send_response(statuses[min(len(received), len(statuses)) - 1])
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass  # the sender closed the connection without reading the body

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/hook", received
    finally:
        server.shutdown()
        server.server_close()


async def queue(runtime, subscription_id: str, sequence: int, *, url: str | None = None) -> str:  # type: ignore[no-untyped-def]
    """Stage one delivery for `subscription_id` directly, optionally to another URL."""
    delivery_id = new_object_id("obx")
    async with transaction(runtime.storage) as session:
        row = await session.get_one(SubscriptionRow, subscription_id)
        target = webhook_target(runtime.keys, row, delivery_id)
        enqueue(
            session,
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            kind="webhook",
            target={**target, "url": url} if url else target,
            payload={"kind": "run.completed", "sequence": sequence, "text": "é"},
            subscription_id=row.id,
            row_id=delivery_id,
        )
    return delivery_id


async def send(runtime, *, attempts: int, policy: EndpointPolicy | None = None) -> None:  # type: ignore[no-untyped-def]
    sender = WebhookSender(runtime.storage, runtime.keys, policy or runtime.endpoint_policy, timeout=5)
    delivery = Delivery(
        runtime.storage,
        {"webhook": sender},
        owner="test",
        policies={"webhook": Policy(batch=10, lease_seconds=30, max_attempts=attempts)},
    )
    await delivery()


async def status(runtime, delivery_id: str) -> tuple[str, int, str | None]:  # type: ignore[no-untyped-def]
    async with short_session(runtime.storage) as session:
        row = await session.get_one(OutboxRow, delivery_id)
        return row.status, row.attempts, row.last_error


async def test_webhooks_are_signed_retried_and_redelivered(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    storage, keys, policy = runtime.storage, runtime.keys, runtime.endpoint_policy
    workspace_id = tenant.workspace_id
    admin = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, workspace_id, BUILT_IN_ROLES["admin"]),)
    )

    with receiver([200, 500, 500, 200]) as (url, received):
        body = SubscriptionCreate.model_validate(
            {"name": "Runs", "url": url, "kinds": ["run.completed"], "signing_secret": SECRET}
        )
        subscription = await subscriptions.create_subscription(
            storage, runtime.access, keys, policy, admin, workspace_id, body, limit=32
        )
        accepted = await queue(runtime, subscription.id, 1)
        await send(runtime, attempts=3)
        assert await status(runtime, accepted) == ("delivered", 1, None)
        headers, sent = received[0]
        assert json.loads(sent) == {"kind": "run.completed", "sequence": 1, "text": "é"}
        assert headers["x-a13n-delivery-id"] == accepted
        timestamp = headers["x-a13n-webhook-timestamp"]
        assert headers["x-a13n-webhook-signature"] == signature(SECRET.encode(), timestamp, accepted, sent)

        retried = await queue(runtime, subscription.id, 2)
        await send(runtime, attempts=3)
        assert await status(runtime, retried) == ("pending", 1, "HTTP 500")
        dead = await queue(runtime, subscription.id, 3)
        await send(runtime, attempts=1)
        assert await status(runtime, dead) == ("dead", 1, "HTTP 500")

        # The policy is applied on every attempt, so a destination that became forbidden is never contacted.
        refused = await queue(runtime, subscription.id, 4, url="http://169.254.169.254/hook")
        await send(runtime, attempts=1)
        refused_status, _, refused_error = await status(runtime, refused)
        assert refused_status == "dead" and refused_error is not None and len(received) == 3

        page = await subscriptions.list_deliveries(
            storage, runtime.access, admin, workspace_id, subscription.id, limit=2, cursor=None
        )
        assert [delivery.id for delivery in page.items] == [refused, dead]
        rest = await subscriptions.list_deliveries(
            storage, runtime.access, admin, workspace_id, subscription.id, limit=2, cursor=page.next_cursor
        )
        assert [delivery.id for delivery in rest.items] == [retried, accepted] and rest.next_cursor is None

        with pytest.raises(ServiceError) as pending:
            await subscriptions.redeliver(storage, runtime.access, admin, workspace_id, subscription.id, retried)
        assert pending.value.details["reason"] == "not_dead"
        restored = await subscriptions.redeliver(storage, runtime.access, admin, workspace_id, subscription.id, dead)
        assert (restored.status, restored.attempts) == ("pending", 0)
        await send(runtime, attempts=1)
        assert await status(runtime, dead) == ("delivered", 1, None)
        assert received[-1][0]["x-a13n-delivery-id"] == dead


async def subscribe(service: SimpleNamespace, url: str, kinds: list[str], **fields: object) -> dict:
    body = {"name": "Runs", "url": url, "kinds": kinds, "signing_secret": SECRET, **fields}
    response = await service.client.post(f"{service.api}/subscriptions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def webhook_rows(service: SimpleNamespace) -> list[OutboxRow]:
    async with short_session(service.runtime.storage) as session:
        return list(await session.scalars(select(OutboxRow).where(OutboxRow.kind == "webhook").order_by(OutboxRow.id)))


async def test_staging_selects_matching_subscriptions_and_binds_each_secret(service: SimpleNamespace) -> None:
    runtime, url = service.runtime, "http://127.0.0.1:9/hook"
    run = RunFacts(service.tenant.workspace_id, new_object_id("ap"), new_object_id("ses"), new_object_id("thr"))
    every = await subscribe(service, url, ["run.accepted", "run.completed"])
    narrow = await subscribe(
        service, url, ["run.completed", "run.failed"], filter={"agent_id": run.agent_id, "thread_id": run.thread_id}
    )
    await subscribe(service, url, ["run.failed"])
    await subscribe(service, url, ["run.completed"], filter={"thread_id": new_object_id("thr")})
    await subscribe(service, url, ["run.completed"], filter={"session_id": new_object_id("ses")})
    await subscribe(service, url, ["run.completed"], enabled=False)

    async with transaction(runtime.storage) as session:
        await stage_webhooks(session, runtime.keys, run, ["run.accepted", "run.completed"], {"seq": 1}, limit=32)
    rows = await webhook_rows(service)
    staged = sorted((row.subscription_id, row.payload["type"]) for row in rows)
    expected = [(every["id"], "run.accepted"), (every["id"], "run.completed"), (narrow["id"], "run.completed")]
    assert staged == sorted(expected)
    for row in rows:
        assert row.payload == {"seq": 1, "type": row.payload["type"], "id": row.id}
        # Each delivery's copy of the secret is bound to its own outbox row.
        envelope = Envelope.model_validate(row.target["signing_secret"])
        own = SecretLocation(row.organization_id, "outbox", "target", row.id)
        assert runtime.keys.reveal(envelope, own) == SECRET.encode()
        other = next(candidate for candidate in rows if candidate.id != row.id)
        with pytest.raises(ServiceError):
            runtime.keys.reveal(envelope, SecretLocation(row.organization_id, "outbox", "target", other.id))

    # The workspace cap bounds one transition's deliveries.
    async with transaction(runtime.storage) as session:
        await stage_webhooks(session, runtime.keys, run, ["run.completed"], {"seq": 2}, limit=1)
    capped = [row.subscription_id for row in await webhook_rows(service) if row.payload["seq"] == 2]
    assert capped == [min(every["id"], narrow["id"])]


async def test_an_undecryptable_signing_secret_never_blocks_a_run(service: SimpleNamespace) -> None:
    broken = await subscribe(service, "http://127.0.0.1:9/hook", ["run.accepted"])
    healthy = await subscribe(service, "http://127.0.0.1:9/hook", ["run.accepted"])
    storage = service.runtime.storage
    async with transaction(storage) as session:
        # Moved onto another row, a ciphertext no longer decrypts, as when its key is gone.
        source = await session.get_one(SubscriptionRow, healthy["id"])
        (await session.get_one(SubscriptionRow, broken["id"])).signing_secret = dict(source.signing_secret)

    run = RunRow(
        id=new_object_id("run"),
        workspace_id=service.tenant.workspace_id,
        session_id=new_object_id("ses"),
        thread_id=new_object_id("thr"),
        agent_id=new_object_id("ap"),
        agent_revision_id=new_object_id("apr"),
        status="queued",
        trigger="message",
    )
    # The last phase of every run transition; its transaction commits whatever the subscriptions hold.
    async with transaction(storage) as session:
        await notify_subscribers(session, service.runtime, run, ["run.accepted"], at=datetime.now(UTC))

    rows = {row.subscription_id: row for row in await webhook_rows(service)}
    dead, pending = rows[broken["id"]], rows[healthy["id"]]
    assert (dead.status, dead.last_error, dead.target) == ("dead", SECRET_UNAVAILABLE, {"url": broken["url"]})
    assert (pending.status, dead.payload["type"], dead.payload["run"]["id"]) == ("pending", "run.accepted", run.id)
    redeliver = f"{service.api}/subscriptions/{broken['id']}/deliveries/{dead.id}/redeliver"
    refused = await service.client.post(redeliver)
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == SECRET_UNAVAILABLE


async def test_the_status_alone_settles_a_delivery(service: SimpleNamespace, runs_kit: SimpleNamespace) -> None:
    await runs_kit.pause_sweeps(service)
    runtime = service.runtime
    # The body is never read, so an accepting receiver may answer with anything.
    with receiver([200], body=bytes(1 << 20)) as (url, received):
        subscription = await subscribe(service, url, ["run.completed"])
        delivered = await queue(runtime, subscription["id"], 1)
        await send(runtime, attempts=1)
        assert await status(runtime, delivered) == ("delivered", 1, None) and len(received) == 1
    # A redirect is an answer, not a new destination.
    with receiver([302], headers={"location": "/elsewhere"}) as (url, received):
        moved = await queue(runtime, subscription["id"], 2, url=url)
        await send(runtime, attempts=1)
        assert await status(runtime, moved) == ("dead", 1, "HTTP 302") and len(received) == 1


async def test_destinations_are_checked_again_at_delivery(service: SimpleNamespace, runs_kit: SimpleNamespace) -> None:
    await runs_kit.pause_sweeps(service)
    runtime = service.runtime
    with receiver([200]) as (url, received):
        subscription = await subscribe(service, url, ["run.completed"])
        refused = await queue(runtime, subscription["id"], 1, url=url)
        await send(runtime, attempts=1, policy=EndpointPolicy(require_https=True))
        assert await status(runtime, refused) == ("dead", 1, "EndpointPolicyError") and received == []
        # An exact HTTP origin exception permits the same destination on the next attempt.
        allowed = await queue(runtime, subscription["id"], 2, url=url)
        policy = EndpointPolicy.from_http_origins(require_https=True, http_origins=(url.removesuffix("/hook"),))
        await send(runtime, attempts=1, policy=policy)
        assert await status(runtime, allowed) == ("delivered", 1, None) and len(received) == 1

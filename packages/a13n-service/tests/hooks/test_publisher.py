from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.durable_operations.outbox import complete_outbox, fail_outbox, redrive_outbox
from a13n_service.hooks import InlineHookSubscriptionInput, WebhookDestinationConfig
from a13n_service.hooks.delivery import DELIVERY_ID_HEADER, SIGNATURE_HEADER, TIMESTAMP_HEADER
from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from a13n_service.hooks.outbox import (
    claim_webhook_deliveries,
)
from a13n_service.hooks.persistence import create_hook_subscription, write_hook_lifecycle
from a13n_service.hooks.publisher import WebhookPublisher
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.domain import LifecycleEventDraft
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.lifecycle.persistence import append_lifecycle_event
from a13n_service.secrets import SecretProtector
from a13n_service.secrets.models import SecretRecord
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from anyio import sleep
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import RUN_ID, SECRET_ID, seed_run_and_secret
from tests.interactions.conftest import NOW, ORGANIZATION_ID, SESSION_ID, THREAD_ID, USER_ID, WORKSPACE_ID
from tests.lifecycle_support import test_lifecycle_writer

pytestmark = pytest.mark.anyio

_SIGNING_VALUE = "publisher-signing-secret"


class _Clock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _AllowEndpoint:
    async def validate(self, endpoint: str, *, resolve_dns: bool = True) -> str:
        assert resolve_dns
        return endpoint


class _SlowEndpoint:
    async def validate(self, endpoint: str, *, resolve_dns: bool = True) -> str:
        del resolve_dns
        await sleep(1)
        return endpoint


def test_settings_require_lease_and_retry_bounds_to_cover_delivery() -> None:
    with pytest.raises(ValueError, match="claim lease must exceed"):
        Settings(webhooks={"claim_lease_seconds": 10, "request_timeout_seconds": 10})
    with pytest.raises(ValueError, match="maximum retry delay"):
        Settings(webhooks={"retry_base_seconds": 10, "retry_max_seconds": 9})


async def _prepare_delivery(
    sessions: async_sessionmaker[AsyncSession],
    *,
    endpoint_url: str = "https://hooks.example.com/foundation",
    managed: bool = False,
    output_reference: dict[str, JsonValue] | None = None,
) -> tuple[str, str, SecretProtector]:
    await seed_run_and_secret(sessions)
    protector = SecretProtector(key=b"0123456789abcdef0123456789abcdef", encryption_key_id="key-v1")
    encrypted = protector.encrypt(
        _SIGNING_VALUE,
        secret_id=SECRET_ID,
        organization_id=ORGANIZATION_ID,
        workspace_id=WORKSPACE_ID,
        owner_type="workspace",
        owner_id=WORKSPACE_ID,
        key="hook-signing",
        version=1,
    )
    async with transaction(sessions) as database:
        secret = await database.get(SecretRecord, SECRET_ID)
        assert secret is not None
        secret.ciphertext = encrypted.ciphertext
        secret.nonce = encrypted.nonce
        secret.encryption_key_id = encrypted.encryption_key_id
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        subscription = await create_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            inline_run_id=None if managed else RUN_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=InlineHookSubscriptionInput(
                hook_names=("run.accepted" if output_reference is None else "run.completed",),
                webhook=WebhookDestinationConfig(
                    endpoint_url=endpoint_url,
                    signing_secret_id=SECRET_ID,
                ),
            ).bind_run_scope(session_id=SESSION_ID, thread_id=THREAD_ID, run_id=RUN_ID),
            now=NOW,
        )
        if output_reference is None:
            await test_lifecycle_writer().append_run_lifecycle(
                database,
                run,
                "run.accepted",
                mutation_id="mut_8181818181818181",
                occurred_at=NOW,
                actor_type="user",
                actor_id=USER_ID,
            )
        else:
            event = await append_lifecycle_event(
                database,
                LifecycleEventDraft(
                    organization_id=ORGANIZATION_ID,
                    entity_type="run",
                    entity_id=RUN_ID,
                    entity_version=run.version,
                    event_type="run.completed",
                    mutation_id="mut_8181818181818181",
                    session_id=SESSION_ID,
                    thread_id=THREAD_ID,
                    run_id=RUN_ID,
                    actor_type="worker",
                    actor_id="wrk_private",
                    occurred_at=NOW,
                    payload={"output_object": output_reference},
                ),
            )
            await write_hook_lifecycle(database, event)
        revision_id = subscription.current_revision_id
    async with short_session(sessions) as database:
        delivery_id = await database.scalar(select(OutboxRecord.id))
    assert delivery_id is not None
    return delivery_id, revision_id, protector


@pytest.mark.parametrize("head_state", ["active", "expired", "paused", "deleted"])
async def test_publisher_sends_signed_canonical_envelope_and_marks_published(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
    head_state: str,
) -> None:
    delivery_id, revision_id, protector = await _prepare_delivery(hook_interaction_sessions)
    if head_state != "active":
        async with transaction(hook_interaction_sessions) as database:
            revision = await database.get(HookSubscriptionRevisionRecord, revision_id)
            head = await database.get(HookSubscriptionRecord, revision.hook_subscription_id)
            if head_state == "expired":
                head.expired_at = NOW
            elif head_state == "paused":
                head.enabled = False
            else:
                head.deleted_at = NOW
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(204, content=b"ack")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        publisher = WebhookPublisher(
            hook_interaction_sessions,
            client,
            _AllowEndpoint(),
            protector,
            clock=_Clock(),
        )
        assert await publisher.publish_once() == 1

    assert len(requests) == 1
    request = requests[0]
    body = json.loads(request.content)
    assert request.method == "POST"
    assert str(request.url) == "https://hooks.example.com/foundation"
    assert body["delivery_id"] == delivery_id
    assert body["hook_subscription_id"].startswith("hsub_")
    assert body["hook_name"] == "run.accepted"
    assert body["resource_seq"] == 1
    assert body["resource_version"] == 1
    assert request.headers[DELIVERY_ID_HEADER] == delivery_id
    assert request.headers[TIMESTAMP_HEADER] == str(int(NOW.timestamp()))
    assert request.headers[SIGNATURE_HEADER].startswith("v1=")

    async with short_session(hook_interaction_sessions) as database:
        record = await database.get(OutboxRecord, delivery_id)
        assert record is not None
        assert record.status == "published"
        assert record.claim_generation == 1
        assert record.attempt_count == 1
        assert record.published_at is not None


async def test_publisher_resolves_the_exact_revision_selected_by_source_commit(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    _, original_revision_id, protector = await _prepare_delivery(hook_interaction_sessions, managed=True)
    async with transaction(hook_interaction_sessions) as database:
        original = await database.get(HookSubscriptionRevisionRecord, original_revision_id)
        assert original is not None
        head = await database.get(HookSubscriptionRecord, original.hook_subscription_id, with_for_update=True)
        assert head is not None
        replacement = HookSubscriptionRevisionRecord(
            id="hsubr_9191919191919191",
            organization_id=original.organization_id,
            workspace_id=original.workspace_id,
            hook_subscription_id=original.hook_subscription_id,
            version=2,
            hook_names=list(original.hook_names),
            session_id=original.session_id,
            thread_id=original.thread_id,
            run_id=original.run_id,
            endpoint_url="https://replacement.example.com/hook",
            signing_secret_id=original.signing_secret_id,
            signature_profile=original.signature_profile,
            created_by_type="user",
            created_by_id=USER_ID,
            created_at=NOW + timedelta(seconds=1),
        )
        database.add(replacement)
        head.version = 2
        head.current_revision_id = replacement.id
        head.updated_at = replacement.created_at
    destinations: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        destinations.append(str(request.url))
        return httpx2.Response(204)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        publisher = WebhookPublisher(
            hook_interaction_sessions,
            client,
            _AllowEndpoint(),
            protector,
            clock=_Clock(),
        )
        await publisher.publish_once()

    assert destinations == ["https://hooks.example.com/foundation"]


@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_error"),
    [
        (302, "dead_lettered", "webhook_http_302"),
        (400, "dead_lettered", "webhook_http_400"),
        (503, "pending", "webhook_http_503"),
    ],
)
async def test_publisher_classifies_http_failures_without_persisting_response_body(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
    status_code: int,
    expected_status: str,
    expected_error: str,
) -> None:
    delivery_id, _, protector = await _prepare_delivery(hook_interaction_sessions)
    secret_response = b"upstream-secret-diagnostic"

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(status_code, content=secret_response))
    ) as client:
        publisher = WebhookPublisher(
            hook_interaction_sessions,
            client,
            _AllowEndpoint(),
            protector,
            clock=_Clock(),
        )
        await publisher.publish_once()

    async with short_session(hook_interaction_sessions) as database:
        record = await database.get(OutboxRecord, delivery_id)
        assert record is not None
        assert record.status == expected_status
        assert record.last_error_code == expected_error
        assert secret_response.decode() not in repr(record.__dict__)


async def test_deleted_signing_secret_dead_letters_without_http(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id, _, protector = await _prepare_delivery(hook_interaction_sessions)
    async with transaction(hook_interaction_sessions) as database:
        secret = await database.get(SecretRecord, SECRET_ID)
        assert secret is not None
        secret.deleted_at = NOW
        secret.ciphertext = None
        secret.nonce = None
        secret.encryption_key_id = None

    def unexpected(_request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("deleted Secret must prevent Webhook delivery")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(unexpected)) as client:
        publisher = WebhookPublisher(
            hook_interaction_sessions,
            client,
            _AllowEndpoint(),
            protector,
            clock=_Clock(),
        )
        await publisher.publish_once()

    async with short_session(hook_interaction_sessions) as database:
        record = await database.get(OutboxRecord, delivery_id)
        assert record is not None
        assert record.status == "dead_lettered"
        assert record.last_error_code == "webhook_signing_secret_unavailable"


async def test_transport_failure_schedules_bounded_retry(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id, _, protector = await _prepare_delivery(hook_interaction_sessions)

    def fail_connect(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection failed", request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fail_connect)) as client:
        publisher = WebhookPublisher(
            hook_interaction_sessions,
            client,
            _AllowEndpoint(),
            protector,
            clock=_Clock(),
        )
        await publisher.publish_once()

    async with short_session(hook_interaction_sessions) as database:
        record = await database.get(OutboxRecord, delivery_id)
        assert record is not None
        assert record.status == "pending"
        assert record.available_at.replace(tzinfo=UTC) == NOW + timedelta(seconds=2)
        assert record.last_error_code == "webhook_transport_failed"


async def test_delivery_deadline_includes_destination_resolution(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id, _, protector = await _prepare_delivery(hook_interaction_sessions)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: httpx2.Response(204))) as client:
        publisher = WebhookPublisher(
            hook_interaction_sessions,
            client,
            _SlowEndpoint(),
            protector,
            delivery_timeout_seconds=0.001,
            clock=_Clock(),
        )
        await publisher.publish_once()

    async with short_session(hook_interaction_sessions) as database:
        record = await database.get(OutboxRecord, delivery_id)
        assert record is not None
        assert record.status == "pending"
        assert record.last_error_code == "webhook_delivery_timed_out"


async def test_claim_generation_fences_stale_completion_and_redrive_reuses_row(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id, revision_id, _ = await _prepare_delivery(hook_interaction_sessions)
    async with transaction(hook_interaction_sessions) as database:
        first = (
            await claim_webhook_deliveries(
                database,
                now=NOW,
                lease_duration=timedelta(seconds=5),
                limit=1,
            )
        )[0]
    reclaimed_at = NOW + timedelta(seconds=5)
    async with transaction(hook_interaction_sessions) as database:
        second = (
            await claim_webhook_deliveries(
                database,
                now=reclaimed_at,
                lease_duration=timedelta(seconds=5),
                limit=1,
            )
        )[0]
        assert second.generation == first.generation + 1
    async with transaction(hook_interaction_sessions) as database:
        assert not await complete_outbox(database, first, completed_at=reclaimed_at)
        assert await fail_outbox(
            database,
            second,
            failed_at=reclaimed_at,
            error_code="webhook_transport_failed",
            retryable=True,
            retry_after=timedelta(seconds=2),
            max_attempts=3,
        )
    async with transaction(hook_interaction_sessions) as database:
        assert (
            await claim_webhook_deliveries(
                database,
                now=reclaimed_at + timedelta(seconds=1),
                lease_duration=timedelta(seconds=5),
                limit=1,
            )
            == ()
        )
        final = (
            await claim_webhook_deliveries(
                database,
                now=reclaimed_at + timedelta(seconds=2),
                lease_duration=timedelta(seconds=5),
                limit=1,
            )
        )[0]
        assert final.attempt_count == 3
        assert await fail_outbox(
            database,
            final,
            failed_at=reclaimed_at + timedelta(seconds=2),
            error_code="webhook_transport_failed",
            retryable=True,
            retry_after=timedelta(seconds=2),
            max_attempts=3,
        )
    async with transaction(hook_interaction_sessions) as database:
        assert await redrive_outbox(
            database,
            outbox_id=delivery_id,
            source_kind="lifecycle_event",
            destination_kind="webhook",
            destination_ref=revision_id,
            redriven_at=reclaimed_at + timedelta(seconds=3),
        )
    async with short_session(hook_interaction_sessions) as database:
        record = await database.get(OutboxRecord, delivery_id)
        assert record is not None
        assert record.status == "pending"
        assert record.attempt_count == 0
        assert record.claim_generation == final.generation
        assert record.last_error_code is None


@pytest.mark.parametrize(
    ("response_size", "expected_status", "expected_error"),
    [(8, "published", None), (9, "dead_lettered", "webhook_response_too_large")],
)
async def test_response_limit_is_enforced_at_the_exact_boundary(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
    response_size: int,
    expected_status: str,
    expected_error: str | None,
) -> None:
    delivery_id, _, protector = await _prepare_delivery(hook_interaction_sessions)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(200, content=b"x" * response_size))
    ) as client:
        publisher = WebhookPublisher(
            hook_interaction_sessions,
            client,
            _AllowEndpoint(),
            protector,
            max_response_bytes=8,
            clock=_Clock(datetime(2026, 9, 3, 0, 30, tzinfo=UTC)),
        )
        await publisher.publish_once()

    async with short_session(hook_interaction_sessions) as database:
        record = await database.get(OutboxRecord, delivery_id)
        assert record is not None
        assert record.status == expected_status
        assert record.last_error_code == expected_error


async def test_postgresql_reclaim_fences_stale_publisher(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    _, _, _ = await _prepare_delivery(hook_interaction_sessions)
    async with transaction(hook_interaction_sessions) as database:
        first = (await claim_webhook_deliveries(database, now=NOW, lease_duration=timedelta(seconds=1), limit=1))[0]
    reclaimed_at = NOW + timedelta(seconds=2)
    async with transaction(hook_interaction_sessions) as database:
        second = (
            await claim_webhook_deliveries(
                database,
                now=reclaimed_at,
                lease_duration=timedelta(seconds=1),
                limit=1,
            )
        )[0]
    async with transaction(hook_interaction_sessions) as database:
        assert not await complete_outbox(database, first, completed_at=reclaimed_at)
        assert await complete_outbox(database, second, completed_at=reclaimed_at)


async def test_webhook_delivery_omits_legacy_output_storage_locator(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id, _, protector = await _prepare_delivery(
        hook_interaction_sessions,
        output_reference={"object_key": "private/output.json", "size_bytes": 256},
    )
    requests = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(204)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        publisher = WebhookPublisher(hook_interaction_sessions, client, _AllowEndpoint(), protector, clock=_Clock())
        assert await publisher.publish_once() == 1
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["delivery_id"] == delivery_id
    assert body["payload"] == {"output_object": {"size_bytes": 256}}
    assert b"private/output.json" not in requests[0].content
    async with short_session(hook_interaction_sessions) as database:
        record = await database.scalar(select(LifecycleEventRecord))
        assert record.payload["output_object"]["object_key"] == "private/output.json"

import json
from asyncio import gather
from datetime import timedelta

from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_domain import RetryableInputOutcome
from a13n_service.connectivity.ingress.admission_models import (
    AgentThreadBindingRecord,
    IngressAdmissionRecord,
    IngressBatchRecord,
)
from a13n_service.connectivity.ingress.provider import ProviderRequest
from a13n_service.connectivity.ingress.reconciler import IngressAdmissionReconciler
from a13n_service.connectivity.ingress.retention import IngressRetentionReconciler
from a13n_service.secrets import SecretProtector
from a13n_service.storage import transaction
from sqlalchemy import func, select

from .conftest import ACCOUNT_ID, NOW, adapter_registry


def _request(event_id, *, channel="support", text="hello"):
    return ProviderRequest(
        headers={"authorization": "Bearer secret-value", "content-type": "application/json"},
        content_type="application/json",
        body=json.dumps(
            {"installation_id": "installation-1", "event_id": event_id, "channel": channel, "text": text},
            sort_keys=True,
        ).encode(),
    )


def _event_service(sessions, protector, *, clock=lambda: NOW, pending_max_count=100):
    return IngressEventService(
        sessions,
        adapter_registry(),
        protector,
        request_max_bytes=1024 * 1024,
        workspace_pending_max_count=pending_max_count,
        workspace_pending_max_bytes=1024 * 1024,
        account_pending_max_count=pending_max_count,
        account_pending_max_bytes=1024 * 1024,
        batch_max_bytes=1024 * 1024,
        dedup_horizon_seconds=3600,
        clock=clock,
    )


class RetryAcceptor:
    def __init__(self):
        self.batches = []

    async def accept_ingress_batch(self, batch):
        self.batches.append(batch)
        return RetryableInputOutcome(reason_code="transient", available_at=NOW)


def reconciler(sessions, acceptor, *, instance="pod-a", clock=lambda: NOW):
    return IngressAdmissionReconciler(
        sessions,
        acceptor,
        instance_id=instance,
        poll_interval_seconds=0.01,
        lease_seconds=5,
        backoff_steps=5,
        max_backoff_seconds=30,
        input_max_bytes=1024 * 1024,
        clock=clock,
    )


def test_provider_repr_omits_secrets():
    request = _request("event-secret", text="message-secret")
    assert "secret-value" not in repr(request) and "message-secret" not in repr(request)


async def test_durable_admission_dedup_and_direct_batch_reference(ingress_event_service, connectivity_sessions):
    response = await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("event-1"))
    replay = await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("event-1"))
    assert response.status_code == replay.status_code == 202
    assert json.loads(replay.body)["duplicate"] is True
    async with connectivity_sessions() as session:
        events = (await session.scalars(select(IngressAdmissionRecord))).all()
        batches = (await session.scalars(select(IngressBatchRecord))).all()
        binding = await session.scalar(select(AgentThreadBindingRecord))
        assert len(events) == len(batches) == 1
        assert events[0].batch_id == batches[0].id
        assert binding.thread_id is None
        assert not hasattr(events[0], "raw_ref_json")
        assert not hasattr(events[0], "status")


async def test_identity_conflict_preserves_first_content(ingress_event_service, connectivity_sessions):
    await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("same", text="first"))
    response = await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("same", text="changed"))
    assert response.status_code == 503
    assert response.body == b"delivery_identity_conflict"
    async with connectivity_sessions() as session:
        event = await session.scalar(select(IngressAdmissionRecord))
        assert event.event_json["text"] == "first"


async def test_first_batch_immediate_ordered_and_claim_freezes_membership(ingress_event_service, connectivity_sessions):
    await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("event-2", text="second"))
    await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("event-1", text="first"))
    acceptor = RetryAcceptor()
    worker = reconciler(connectivity_sessions, acceptor)
    assert await worker.run_once()
    first = acceptor.batches[0]
    assert [event["text"] for event in first.agent_input.structured_content["events"]] == ["first", "second"]
    await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("event-3", text="third"))
    assert await worker.run_once()
    assert acceptor.batches[1].batch_id == first.batch_id
    assert acceptor.batches[1].agent_input == first.agent_input
    async with connectivity_sessions() as session:
        batches = (await session.scalars(select(IngressBatchRecord).order_by(IngressBatchRecord.sequence))).all()
        assert [b.event_count for b in batches] == [2, 1]
        assert batches[0].claim_generation == 2


async def test_frequency_barrier_blocks_full_and_later_batches(ingress_event_service, connectivity_sessions):
    await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("first"))
    worker = reconciler(connectivity_sessions, RetryAcceptor())
    claim = await worker._claim()
    assert claim is not None
    async with transaction(connectivity_sessions) as session:
        batch = await session.get(IngressBatchRecord, claim.batch_id)
        binding = await session.get(AgentThreadBindingRecord, batch.binding_id)
        batch.status = "accepted"
        batch.terminal_at = NOW
        binding.next_submission_at = NOW + timedelta(milliseconds=100)
    for i in range(12):
        await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request(f"later-{i}"))
    assert not await worker.run_once()
    advanced = reconciler(connectivity_sessions, RetryAcceptor(), clock=lambda: NOW + timedelta(milliseconds=100))
    assert await advanced.run_once()
    assert advanced._acceptor.batches[0].agent_input.structured_content["events"]


async def test_capacity_rejection_does_not_ack_or_drop_existing(connectivity_sessions, credential_protector):
    service = _event_service(connectivity_sessions, credential_protector, pending_max_count=1)
    assert (await service.receive(account_id=ACCOUNT_ID, request=_request("one"))).status_code == 202
    assert (await service.receive(account_id=ACCOUNT_ID, request=_request("two"))).status_code == 503
    async with connectivity_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 1


async def test_reception_disable_is_irrelevant_without_new_storage(ingress_event_service, connectivity_sessions):
    async with transaction(connectivity_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        account.receive_enabled = False
    response = await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("event"))
    assert json.loads(response.body)["status"] == "irrelevant"
    async with connectivity_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 0


async def test_retention_never_expires_pending_payload(ingress_event_service, connectivity_sessions):
    await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("one"))
    retention = IngressRetentionReconciler(connectivity_sessions, clock=lambda: NOW + timedelta(days=2))
    assert await retention.reconcile_once() == 0
    async with transaction(connectivity_sessions) as session:
        batch = await session.scalar(select(IngressBatchRecord))
        batch.status = "rejected"
        batch.terminal_at = NOW
        batch.rejection_reason = "invalid"
    assert await retention.reconcile_once() == 1
    async with connectivity_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 0


async def test_postgresql_duplicate_and_claim_ordering(connectivity_sessions):
    sessions = connectivity_sessions
    service = _event_service(sessions, SecretProtector(key=b"k" * 32, encryption_key_id="connectivity-test"))
    responses = await gather(*(service.receive(account_id=ACCOUNT_ID, request=_request("one")) for _ in range(2)))
    assert sorted(json.loads(r.body)["duplicate"] for r in responses) == [False, True]
    worker = reconciler(sessions, RetryAcceptor())
    claim = await worker._claim()
    assert claim is not None
    await service.receive(account_id=ACCOUNT_ID, request=_request("two"))
    other = reconciler(sessions, RetryAcceptor(), instance="pod-b")
    assert await other._claim() is None
    async with transaction(sessions) as session:
        await session.scalar(
            select(IngressBatchRecord).where(IngressBatchRecord.id == claim.batch_id).with_for_update()
        )
        # The older in-flight batch remains a barrier even when skip_locked could
        # otherwise expose the second batch to another Pod.
        assert await other._claim() is None
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AgentThreadBindingRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 2


async def test_postgresql_append_claim_race_preserves_every_event(connectivity_sessions):
    sessions = connectivity_sessions
    service = _event_service(sessions, SecretProtector(key=b"k" * 32, encryption_key_id="connectivity-test"))
    await service.receive(account_id=ACCOUNT_ID, request=_request("first"))
    worker = reconciler(sessions, RetryAcceptor())
    claim, response = await gather(worker._claim(), service.receive(account_id=ACCOUNT_ID, request=_request("racing")))
    assert claim is not None and response.status_code == 202
    prepared = await worker._prepare(claim)
    original = prepared.agent_input
    await service.receive(account_id=ACCOUNT_ID, request=_request("after-claim"))
    assert (await worker._prepare(claim)).agent_input == original
    async with sessions() as session:
        events = (await session.scalars(select(IngressAdmissionRecord))).all()
        batches = (await session.scalars(select(IngressBatchRecord))).all()
        assert len(events) == 3
        assert sum(batch.event_count for batch in batches) == 3
        for batch in batches:
            assert sum(event.batch_id == batch.id for event in events) == batch.event_count


async def test_postgresql_concurrent_capacity_never_acknowledges_overflow(connectivity_sessions):
    sessions = connectivity_sessions
    service = _event_service(
        sessions, SecretProtector(key=b"k" * 32, encryption_key_id="connectivity-test"), pending_max_count=1
    )
    responses = await gather(
        *(service.receive(account_id=ACCOUNT_ID, request=_request(str(i), channel=str(i))) for i in range(2))
    )
    assert sorted(response.status_code for response in responses) == [202, 503]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 1


async def test_configured_targets_reject_unlisted_groups_before_binding(
    ingress_event_service, connectivity_sessions, target_service
):
    from a13n_service.connectivity.accounts.targets import TargetConfig

    from .conftest import actor

    async with transaction(connectivity_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        account.reception_scope = "configured_targets"
    await target_service.create(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key="pilot",
        request=TargetConfig(target_kind="conversation", external_target_id="support"),
    )
    await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("outside", channel="private"))
    async with connectivity_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IngressBatchRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(AgentThreadBindingRecord)) == 0
    await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("pilot", channel="support"))
    async with connectivity_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IngressBatchRecord)) == 1


async def test_reception_scope_change_preserves_acknowledged_batch(ingress_event_service, connectivity_sessions):
    await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("accepted"))
    async with transaction(connectivity_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        account.reception_scope = "configured_targets"
    await ingress_event_service.receive(account_id=ACCOUNT_ID, request=_request("unlisted"))
    acceptor = RetryAcceptor()
    assert await reconciler(connectivity_sessions, acceptor).run_once()
    assert len(acceptor.batches) == 1
    async with connectivity_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IngressBatchRecord)) == 1

from __future__ import annotations

import json
from asyncio import gather
from datetime import timedelta

import httpx2
import pytest
from a13n_service.api import install_api_conventions
from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_domain import (
    AcceptedInputOutcome,
    InputAcceptanceOutcome,
    PreparedIngressBatch,
    RetryableInputOutcome,
)
from a13n_service.connectivity.ingress.admission_models import (
    IngressAdmissionRecord,
    IngressBatchEventRecord,
    IngressBatchRecord,
)
from a13n_service.connectivity.ingress.data_router import router as ingress_data_router
from a13n_service.connectivity.ingress.domain import IngressStatus, UpdateRouteRequest
from a13n_service.connectivity.ingress.provider import ProviderRequest
from a13n_service.connectivity.ingress.raw_objects import IngressRawObjectStore
from a13n_service.connectivity.ingress.reconciler import IngressAdmissionReconciler
from a13n_service.connectivity.ingress.routes import RouteService
from a13n_service.connectivity.ingress.service import IngressService
from a13n_service.secrets import InternalSecretService, SecretProtector
from a13n_service.settings import ServiceSettings
from a13n_service.storage.object_store import LocalObjectStore
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, WORKSPACE_ID, FakeIngressAdapter, actor
from .test_ingress_service import ingress_request, route_request


def _request(
    event_id: str, *, channel: str = "support", text: str = "hello", retain_raw: bool = False
) -> ProviderRequest:
    return ProviderRequest(
        headers={"authorization": "Bearer secret-value", "content-type": "application/json"},
        content_type="application/json",
        body=json.dumps(
            {
                "installation_id": "installation-1",
                "event_id": event_id,
                "channel": channel,
                "text": text,
                "retain_raw": retain_raw,
            },
            sort_keys=True,
        ).encode(),
    )


def test_provider_contract_repr_omits_sensitive_delivery_content() -> None:
    request = _request("secret-event", text="secret-message")

    assert "secret-value" not in repr(request)
    assert "secret-message" not in repr(request)
    assert request.headers["authorization"] == "Bearer secret-value"


async def _create_ingress(service: IngressService) -> str:
    resource = await service.create_ingress(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="admission-ingress",
        request=ingress_request(),
    )
    return resource.id


def _event_service(
    sessions: async_sessionmaker[AsyncSession],
    secrets: InternalSecretService,
    objects: LocalObjectStore,
    registry: AdapterRegistry[IngressAdapter],
    *,
    pending_max_count: int = 100,
) -> IngressEventService:
    return IngressEventService(
        sessions,
        registry,
        secrets,
        IngressRawObjectStore(objects),
        request_max_bytes=1024 * 1024,
        raw_retention_seconds=3600,
        workspace_pending_max_count=pending_max_count,
        workspace_pending_max_bytes=1024 * 1024,
        ingress_pending_max_count=pending_max_count,
        ingress_pending_max_bytes=1024 * 1024,
        batch_max_bytes=1024 * 1024,
        dedup_horizon_seconds=3600,
        clock=lambda: NOW,
    )


@pytest.mark.anyio
async def test_provider_event_is_durable_before_ack_and_deduplicated(
    ingress_service: IngressService,
    ingress_event_service: IngressEventService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    ingress_id = await _create_ingress(ingress_service)

    response = await ingress_event_service.receive(ingress_id=ingress_id, request=_request("event-1"))
    replay = await ingress_event_service.receive(ingress_id=ingress_id, request=_request("event-1"))

    assert response.status_code == replay.status_code == 202
    assert json.loads(response.body)["status"] == "pending"
    assert json.loads(replay.body)["duplicate"] is True
    async with connectivity_sessions() as session:
        admissions = tuple((await session.scalars(select(IngressAdmissionRecord))).all())
        batches = tuple((await session.scalars(select(IngressBatchRecord))).all())
        links = tuple((await session.scalars(select(IngressBatchEventRecord))).all())
    assert len(admissions) == len(batches) == len(links) == 1
    assert admissions[0].id.startswith("iadm_")
    assert batches[0].id.startswith("ibat_")
    assert admissions[0].status == batches[0].status == "pending"
    assert admissions[0].request_digest not in response.body.decode()


@pytest.mark.anyio
async def test_postgresql_concurrent_duplicate_delivery_creates_one_admission(
    postgres_connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
    ingress_adapter_registry: AdapterRegistry[IngressAdapter],
) -> None:
    secrets = InternalSecretService(
        postgres_connectivity_sessions,
        SecretProtector(key=b"k" * 32, encryption_key_id="connectivity-test"),
        clock=lambda: NOW,
    )
    ingress_service = IngressService(
        postgres_connectivity_sessions,
        ingress_adapter_registry,
        secrets,
        clock=lambda: NOW,
    )
    ingress_id = await _create_ingress(ingress_service)
    event_service = _event_service(
        postgres_connectivity_sessions,
        secrets,
        connectivity_objects,
        ingress_adapter_registry,
    )

    responses = await gather(
        event_service.receive(ingress_id=ingress_id, request=_request("concurrent-event")),
        event_service.receive(ingress_id=ingress_id, request=_request("concurrent-event")),
    )

    assert [response.status_code for response in responses] == [202, 202]
    assert sorted(json.loads(response.body)["duplicate"] for response in responses) == [False, True]
    async with postgres_connectivity_sessions() as session:
        admission_count = await session.scalar(select(func.count()).select_from(IngressAdmissionRecord))
        batch_count = await session.scalar(select(func.count()).select_from(IngressBatchRecord))
        link_count = await session.scalar(select(func.count()).select_from(IngressBatchEventRecord))
    assert (admission_count, batch_count, link_count) == (1, 1, 1)


@pytest.mark.anyio
async def test_data_plane_streams_into_provider_adapter_with_bounded_failures(
    ingress_service: IngressService,
    ingress_event_service: IngressEventService,
    service_runtime_factory,
) -> None:
    ingress_id = await _create_ingress(ingress_service)
    app = FastAPI()
    install_api_conventions(app)
    app.state.runtime = service_runtime_factory(
        settings=ServiceSettings(_env_file=None, connectivity_provider_request_max_bytes=1024),
        ingress_events=ingress_event_service,
    )
    app.include_router(ingress_data_router)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        accepted = await client.post(
            f"/connectivity/v1/ingresses/{ingress_id}/events",
            content=_request("event-http").body,
            headers={"authorization": "Bearer secret-value", "content-type": "application/json"},
        )
        oversized = await client.post(
            f"/connectivity/v1/ingresses/{ingress_id}/events",
            content=b"x" * 1025,
        )

    assert accepted.status_code == 202
    assert accepted.json()["status"] == "pending"
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "request_too_large"


@pytest.mark.anyio
async def test_delivery_identity_conflict_keeps_first_admission(
    ingress_service: IngressService,
    ingress_event_service: IngressEventService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    ingress_id = await _create_ingress(ingress_service)
    await ingress_event_service.receive(ingress_id=ingress_id, request=_request("event-1"))

    conflict = await ingress_event_service.receive(
        ingress_id=ingress_id,
        request=_request("event-1", text="changed"),
    )

    assert conflict.status_code == 503
    assert conflict.body == b"delivery_identity_conflict"
    async with connectivity_sessions() as session:
        count = await session.scalar(select(func.count()).select_from(IngressAdmissionRecord))
        admission = await session.scalar(select(IngressAdmissionRecord))
    assert count == 1
    assert admission is not None
    assert admission.event_json["text"] == "hello"


@pytest.mark.anyio
async def test_runtime_route_ambiguity_is_durably_rejected(
    ingress_service: IngressService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_secrets: InternalSecretService,
    connectivity_objects: LocalObjectStore,
) -> None:
    class RuntimeAmbiguousAdapter(FakeIngressAdapter):
        allows_runtime_ambiguity = True

        def prove_non_overlap(self, left: dict[str, object], right: dict[str, object]) -> bool | None:
            del left, right
            return None

    registry = AdapterRegistry[IngressAdapter](
        (
            AdapterDefinition[IngressAdapter](
                key="fake",
                config_versions=frozenset({"fake_http_v1"}),
                factory=RuntimeAmbiguousAdapter,
            ),
        )
    )
    ingress_id = await _create_ingress(ingress_service)
    routes = RouteService(
        connectivity_sessions,
        registry,
        batch_max_events=100,
        batch_max_wait_seconds=300,
        clock=lambda: NOW,
    )
    await routes.create_route(
        actor=actor(), ingress_id=ingress_id, idempotency_key="ambiguous-1", request=route_request()
    )
    await routes.create_route(
        actor=actor(),
        ingress_id=ingress_id,
        idempotency_key="ambiguous-2",
        request=route_request().model_copy(update={"name": "Second matching route"}),
    )
    service = _event_service(connectivity_sessions, connectivity_secrets, connectivity_objects, registry)

    response = await service.receive(ingress_id=ingress_id, request=_request("event-1"))

    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "rejected"
    assert json.loads(response.body)["reason_code"] == "route_ambiguous"
    async with connectivity_sessions() as session:
        admission = await session.scalar(select(IngressAdmissionRecord))
        batch_count = await session.scalar(select(func.count()).select_from(IngressBatchRecord))
    assert admission is not None
    assert admission.status == "rejected"
    assert batch_count == 0


@pytest.mark.anyio
async def test_compatible_events_batch_and_reconciler_accepts_once(
    ingress_service: IngressService,
    ingress_event_service: IngressEventService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    ingress_id = await _create_ingress(ingress_service)
    await ingress_event_service.receive(ingress_id=ingress_id, request=_request("event-2", text="second"))
    await ingress_event_service.receive(ingress_id=ingress_id, request=_request("event-1", text="first"))
    acceptor = _AcceptingInputAcceptor()
    reconciler = IngressAdmissionReconciler(
        connectivity_sessions,
        acceptor,
        instance_id="reconciler-1",
        poll_interval_seconds=1,
        lease_seconds=30,
        max_attempts=5,
        max_backoff_seconds=60,
        clock=lambda: NOW,
    )

    assert await reconciler.run_once() is True

    assert len(acceptor.batches) == 1
    prepared = acceptor.batches[0]
    assert [event["text"] for event in prepared.agent_input.structured_content] == ["first", "second"]
    assert prepared.provider_context == {"channel": "support"}
    assert prepared.provider_context_version == "fake_v1"
    assert prepared.binding_state == "unbound"
    async with connectivity_sessions() as session:
        batch = await session.scalar(select(IngressBatchRecord))
        statuses = tuple((await session.scalars(select(IngressAdmissionRecord.status))).all())
    assert batch is not None
    assert batch.status == "accepted"
    assert batch.result_kind == "run"
    assert statuses == ("accepted", "accepted")


@pytest.mark.anyio
async def test_unavailable_input_bridge_releases_claim_without_losing_batch(
    ingress_service: IngressService,
    ingress_event_service: IngressEventService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    ingress_id = await _create_ingress(ingress_service)
    await ingress_event_service.receive(ingress_id=ingress_id, request=_request("event-1"))
    reconciler = IngressAdmissionReconciler(
        connectivity_sessions,
        _RetryingInputAcceptor(),
        instance_id="reconciler-1",
        poll_interval_seconds=1,
        lease_seconds=30,
        max_attempts=5,
        max_backoff_seconds=60,
        clock=lambda: NOW,
    )

    assert await reconciler.run_once() is True

    async with connectivity_sessions() as session:
        batch = await session.scalar(select(IngressBatchRecord))
        admission = await session.scalar(select(IngressAdmissionRecord))
    assert batch is not None and admission is not None
    assert batch.status == admission.status == "pending"
    assert batch.claim_owner is None
    assert batch.claim_expires_at is None
    assert batch.attempt_count == 1
    assert batch.available_at == NOW.replace(tzinfo=None) + timedelta(seconds=30)


@pytest.mark.anyio
async def test_changed_route_rejects_frozen_batch_without_rerouting(
    ingress_service: IngressService,
    route_service: RouteService,
    ingress_event_service: IngressEventService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    ingress_id = await _create_ingress(ingress_service)
    route = await route_service.create_route(
        actor=actor(),
        ingress_id=ingress_id,
        idempotency_key="frozen-route",
        request=route_request(),
    )
    await ingress_event_service.receive(ingress_id=ingress_id, request=_request("event-1"))
    await route_service.update_route(
        actor=actor(),
        route_id=route.id,
        request=UpdateRouteRequest(expected_version=1, name="Changed after admission"),
    )
    acceptor = _AcceptingInputAcceptor()
    reconciler = IngressAdmissionReconciler(
        connectivity_sessions,
        acceptor,
        instance_id="reconciler-1",
        poll_interval_seconds=1,
        lease_seconds=30,
        max_attempts=5,
        max_backoff_seconds=60,
        clock=lambda: NOW,
    )

    assert await reconciler.run_once() is True

    assert acceptor.batches == []
    async with connectivity_sessions() as session:
        batch = await session.scalar(select(IngressBatchRecord))
        admission = await session.scalar(select(IngressAdmissionRecord))
    assert batch is not None and admission is not None
    assert batch.status == admission.status == "rejected"
    assert batch.rejection_reason == admission.rejection_reason == "route_changed"


@pytest.mark.anyio
async def test_acceptor_crash_is_retryable_and_keeps_durable_input(
    ingress_service: IngressService,
    ingress_event_service: IngressEventService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    ingress_id = await _create_ingress(ingress_service)
    await ingress_event_service.receive(ingress_id=ingress_id, request=_request("event-1"))
    reconciler = IngressAdmissionReconciler(
        connectivity_sessions,
        _CrashingInputAcceptor(),
        instance_id="reconciler-1",
        poll_interval_seconds=2,
        lease_seconds=30,
        max_attempts=5,
        max_backoff_seconds=60,
        clock=lambda: NOW,
    )

    assert await reconciler.run_once() is True

    async with connectivity_sessions() as session:
        batch = await session.scalar(select(IngressBatchRecord))
        admission = await session.scalar(select(IngressAdmissionRecord))
    assert batch is not None and admission is not None
    assert batch.status == admission.status == "pending"
    assert batch.claim_owner is None
    assert batch.available_at == NOW.replace(tzinfo=None) + timedelta(seconds=2)


@pytest.mark.anyio
async def test_capacity_exhaustion_never_creates_ack_eligible_state(
    ingress_service: IngressService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_secrets: InternalSecretService,
    connectivity_objects: LocalObjectStore,
    ingress_adapter_registry: AdapterRegistry[IngressAdapter],
) -> None:
    ingress_id = await _create_ingress(ingress_service)
    service = _event_service(
        connectivity_sessions,
        connectivity_secrets,
        connectivity_objects,
        ingress_adapter_registry,
        pending_max_count=0,
    )

    response = await service.receive(ingress_id=ingress_id, request=_request("event-1", retain_raw=True))

    assert response.status_code == 503
    assert response.body == b"admission_capacity_exhausted"
    async with connectivity_sessions() as session:
        count = await session.scalar(select(func.count()).select_from(IngressAdmissionRecord))
    assert count == 0
    assert (await connectivity_objects.list()).items == ()


@pytest.mark.anyio
async def test_irrelevant_event_has_no_receipt_row_or_orphan_raw_object(
    ingress_service: IngressService,
    route_service: RouteService,
    ingress_event_service: IngressEventService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    ingress_id = await _create_ingress(ingress_service)
    route = await route_service.create_route(
        actor=actor(),
        ingress_id=ingress_id,
        idempotency_key="disabled-route",
        request=route_request(),
    )
    await route_service.update_route(
        actor=actor(),
        route_id=route.id,
        request=UpdateRouteRequest(expected_version=1, enabled=False),
    )

    response = await ingress_event_service.receive(
        ingress_id=ingress_id,
        request=_request("event-1", retain_raw=True),
    )

    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "irrelevant"
    async with connectivity_sessions() as session:
        count = await session.scalar(select(func.count()).select_from(IngressAdmissionRecord))
    assert count == 0
    assert (await connectivity_objects.list()).items == ()


@pytest.mark.anyio
async def test_disabled_ingress_is_deterministically_irrelevant(
    ingress_service: IngressService,
    ingress_event_service: IngressEventService,
) -> None:
    ingress_id = await _create_ingress(ingress_service)
    await ingress_service.set_status(
        actor=actor(),
        ingress_id=ingress_id,
        status=IngressStatus.disabled,
        expected_version=1,
        idempotency_key="disable-ingress",
    )

    response = await ingress_event_service.receive(ingress_id=ingress_id, request=_request("event-1"))

    assert response.status_code == 200
    assert json.loads(response.body)["reason_code"] == "ingress_disabled"


class _AcceptingInputAcceptor:
    def __init__(self) -> None:
        self.batches: list[PreparedIngressBatch] = []

    async def accept_ingress_batch(self, batch: PreparedIngressBatch) -> InputAcceptanceOutcome:
        self.batches.append(batch)
        return AcceptedInputOutcome(receipt_kind="run", receipt_id="run_abcdef1234567890")


class _RetryingInputAcceptor:
    async def accept_ingress_batch(self, batch: PreparedIngressBatch) -> InputAcceptanceOutcome:
        del batch
        return RetryableInputOutcome(
            reason_code="input_bridge_unavailable",
            available_at=NOW + timedelta(seconds=30),
        )


class _CrashingInputAcceptor:
    async def accept_ingress_batch(self, batch: PreparedIngressBatch) -> InputAcceptanceOutcome:
        del batch
        raise RuntimeError("simulated crash")

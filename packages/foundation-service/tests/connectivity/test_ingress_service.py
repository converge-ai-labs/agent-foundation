from __future__ import annotations

import pytest
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.ingress.domain import (
    CreateIngressRequest,
    CreateRouteRequest,
    IngressStatus,
    InputBatchingPolicy,
    ReplaceIngressCredentialsRequest,
    UpdateIngressRequest,
    UpdateRouteRequest,
)
from a13n_service.connectivity.ingress.routes import RouteService
from a13n_service.connectivity.ingress.service import IngressError, IngressService
from a13n_service.connectivity.models import ConnectivityCommandRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import AGENT_ID, SERVICE_ACCOUNT_ID, WORKSPACE_ID, FakeIngressAdapter, actor


def ingress_request() -> CreateIngressRequest:
    return CreateIngressRequest(
        name="Support Slack",
        provider_key="fake",
        provider_config_version="fake_http_v1",
        provider_config={"installation_id": "installation-1"},
        execution_service_account_id=SERVICE_ACCOUNT_ID,
        agents=(AGENT_ID,),
        default_agent_id=AGENT_ID,
        credentials={"token": "secret-value"},
    )


def route_request(*, channel: str = "support") -> CreateRouteRequest:
    return CreateRouteRequest(
        name=f"Route {channel}",
        match={"channel": channel},
        agent_id=AGENT_ID,
        input_mapping=None,
        input_batching=InputBatchingPolicy(min_interval_ms=100, max_batch_events=10),
        capability_overlays={},
        provider_policy={},
    )


@pytest.mark.anyio
async def test_ingress_lifecycle_is_idempotent_safe_and_cas_guarded(
    ingress_service: IngressService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = await ingress_service.create_ingress(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create", request=ingress_request()
    )
    replay = await ingress_service.create_ingress(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create", request=ingress_request()
    )

    assert replay == created
    assert created.credential_configured is True
    assert "secret-value" not in repr(created)
    assert created.version == created.credential_generation == 1
    enabled = await ingress_service.set_status(
        actor=actor(),
        ingress_id=created.id,
        status=IngressStatus.active,
        expected_version=1,
        idempotency_key="already-active",
    )
    assert enabled.version == 1

    async with connectivity_sessions() as session:
        commands = tuple((await session.scalars(select(ConnectivityCommandRecord))).all())
    assert all(command.idempotency_key_digest not in {"create", "already-active"} for command in commands)

    rotated = await ingress_service.replace_credentials(
        actor=actor(),
        ingress_id=created.id,
        idempotency_key="rotate",
        request=ReplaceIngressCredentialsRequest(expected_version=1, credentials={"token": "next-secret"}),
    )
    assert rotated.version == rotated.credential_generation == 2
    assert (
        await ingress_service.replace_credentials(
            actor=actor(),
            ingress_id=created.id,
            idempotency_key="rotate",
            request=ReplaceIngressCredentialsRequest(expected_version=1, credentials={"token": "next-secret"}),
        )
        == rotated
    )

    with pytest.raises(IngressError, match="version") as stale:
        await ingress_service.update_ingress(
            actor=actor(), ingress_id=created.id, request=UpdateIngressRequest(expected_version=1, name="Stale")
        )
    assert stale.value.code == "version_conflict"


@pytest.mark.anyio
async def test_ingress_identity_and_route_overlap_fail_closed(
    ingress_service: IngressService, route_service: RouteService
) -> None:
    ingress = await ingress_service.create_ingress(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create-routes", request=ingress_request()
    )
    with pytest.raises(IngressError) as immutable:
        await ingress_service.update_ingress(
            actor=actor(),
            ingress_id=ingress.id,
            request=UpdateIngressRequest(
                expected_version=1,
                provider_config={"installation_id": "installation-2"},
            ),
        )
    assert immutable.value.code == "immutable_ingress_identity"

    route = await route_service.create_route(
        actor=actor(), ingress_id=ingress.id, idempotency_key="route", request=route_request()
    )
    assert route.version == 1
    with pytest.raises(IngressError) as overlap:
        await route_service.create_route(
            actor=actor(), ingress_id=ingress.id, idempotency_key="overlap", request=route_request()
        )
    assert overlap.value.code == "route_overlap"

    updated = await route_service.update_route(
        actor=actor(),
        route_id=route.id,
        request=UpdateRouteRequest(expected_version=1, match={"channel": "engineering"}),
    )
    assert updated.match == {"channel": "engineering"}
    assert updated.version == 2


@pytest.mark.anyio
async def test_invalid_adapter_and_idempotency_reuse_are_rejected(ingress_service: IngressService) -> None:
    invalid = ingress_request().model_copy(update={"provider_config_version": "unknown_v1"})
    with pytest.raises(IngressError) as unsupported:
        await ingress_service.create_ingress(
            actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="unsupported", request=invalid
        )
    assert unsupported.value.code == "unsupported_ingress_adapter"

    await ingress_service.create_ingress(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="same-key", request=ingress_request()
    )
    with pytest.raises(IngressError) as conflict:
        await ingress_service.create_ingress(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="same-key",
            request=ingress_request().model_copy(update={"name": "Different"}),
        )
    assert conflict.value.code == "idempotency_conflict"

    with pytest.raises(IngressError) as invalid_key:
        await ingress_service.create_ingress(
            actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="not valid", request=ingress_request()
        )
    assert invalid_key.value.code == "invalid_request"


@pytest.mark.anyio
async def test_unknown_route_overlap_requires_explicit_adapter_support(
    ingress_service: IngressService,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    ingress = await ingress_service.create_ingress(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="unknown-ingress", request=ingress_request()
    )

    class UnknownOverlapAdapter(FakeIngressAdapter):
        def prove_non_overlap(self, left: dict[str, object], right: dict[str, object]) -> bool | None:
            del left, right
            return None

    routes = RouteService(
        connectivity_sessions,
        AdapterRegistry(
            (
                AdapterDefinition(
                    key="fake",
                    config_versions=frozenset({"fake_http_v1"}),
                    factory=UnknownOverlapAdapter,
                ),
            )
        ),
        batch_max_events=100,
        batch_max_wait_seconds=300,
    )
    await routes.create_route(actor=actor(), ingress_id=ingress.id, idempotency_key="first", request=route_request())
    with pytest.raises(IngressError) as unknown:
        await routes.create_route(
            actor=actor(),
            ingress_id=ingress.id,
            idempotency_key="second",
            request=route_request(channel="engineering"),
        )
    assert unknown.value.code == "route_overlap_unknown"

from __future__ import annotations

import pytest
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.ingress.domain import (
    CreateIngressRequest,
    CreateRouteRequest,
    IngressStatus,
    InputBatchingPolicy,
    UpdateIngressRequest,
    UpdateRouteRequest,
)
from a13n_service.connectivity.ingress.routes import RouteService
from a13n_service.connectivity.ingress.service import IngressService, NativeError
from a13n_service.connectivity.models import ConnectivityCommandRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import ACCOUNT_ID, AGENT_ID, SERVICE_ACCOUNT_ID, WORKSPACE_ID, FakeIngressAdapter, actor


def ingress_request() -> CreateIngressRequest:
    return CreateIngressRequest(
        name="Support Slack",
        account_id=ACCOUNT_ID,
        provider_config={"events_transport": "http"},
        execution_service_account_id=SERVICE_ACCOUNT_ID,
        agents=(AGENT_ID,),
        default_agent_id=AGENT_ID,
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
    assert created.account_id == ACCOUNT_ID
    assert "secret-value" not in repr(created)
    assert created.version == 1
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

    updated = await ingress_service.update_ingress(
        actor=actor(), ingress_id=created.id, request=UpdateIngressRequest(expected_version=1, name="Renamed")
    )
    assert updated.version == 2

    with pytest.raises(NativeError, match="version") as stale:
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
    with pytest.raises(ValueError):
        UpdateIngressRequest(expected_version=1, account_id="another-account")

    route = await route_service.create_route(
        actor=actor(), ingress_id=ingress.id, idempotency_key="route", request=route_request()
    )
    assert route.version == 1
    with pytest.raises(NativeError) as overlap:
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
    await ingress_service.create_ingress(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="same-key", request=ingress_request()
    )
    with pytest.raises(NativeError) as conflict:
        await ingress_service.create_ingress(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="same-key",
            request=ingress_request().model_copy(update={"name": "Different"}),
        )
    assert conflict.value.code == "idempotency_conflict"

    with pytest.raises(NativeError) as invalid_key:
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
    with pytest.raises(NativeError) as unknown:
        await routes.create_route(
            actor=actor(),
            ingress_id=ingress.id,
            idempotency_key="second",
            request=route_request(channel="engineering"),
        )
    assert unknown.value.code == "route_overlap_unknown"


@pytest.mark.anyio
async def test_route_mapping_is_compiled_on_write(ingress_service: IngressService, route_service: RouteService) -> None:
    ingress = await ingress_service.create_ingress(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="mapping-ingress", request=ingress_request()
    )
    request = route_request().model_copy(
        update={
            "input_mapping": {
                "op": "object",
                "fields": {
                    "schema_version": {"op": "static", "value": "2"},
                    "content": {"op": "static", "value": []},
                },
            }
        }
    )
    route = await route_service.create_route(
        actor=actor(), ingress_id=ingress.id, idempotency_key="mapping-route", request=request
    )
    assert route.input_mapping == {
        "op": "object",
        "fields": {
            "content": {"op": "static", "value": []},
            "schema_version": {"op": "static", "value": "2"},
        },
    }

    with pytest.raises(NativeError) as invalid:
        await route_service.create_route(
            actor=actor(),
            ingress_id=ingress.id,
            idempotency_key="invalid-mapping",
            request=route_request(channel="invalid").model_copy(
                update={"input_mapping": {"op": "select", "path": ["raw_ref"]}}
            ),
        )
    assert invalid.value.code == "invalid_input_mapping"

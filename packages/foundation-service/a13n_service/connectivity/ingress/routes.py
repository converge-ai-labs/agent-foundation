"""Transactional Route resource management."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.adapters import IngressAdapter, JsonObject
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.cursors import CursorError, decode_cursor, encode_cursor
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.management import fingerprint, record_command
from a13n_service.connectivity.native_management import (
    audit,
    authorize,
    idempotency_key_digest,
    replay_command,
    require_adapter,
    require_limit,
    require_version,
)
from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from ._management import ingress_agent_ids, require_ingress, require_non_overlapping, require_route
from .domain import (
    CreateRouteRequest,
    InputBatchingPolicy,
    Route,
    RouteCollection,
    UpdateRouteRequest,
)
from .mapping import MappingError, compile_mapping
from .models import IngressRecord, RouteRecord


class RouteService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterRegistry[IngressAdapter],
        *,
        batch_max_events: int,
        batch_max_wait_seconds: float,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._batch_max_events = batch_max_events
        self._batch_max_wait_ms = round(batch_max_wait_seconds * 1000)
        self._clock = clock

    async def create_route(
        self,
        *,
        actor: AuthenticatedActor,
        ingress_id: str,
        idempotency_key: str,
        request: CreateRouteRequest,
    ) -> Route:
        key_digest = idempotency_key_digest(idempotency_key)
        request_fingerprint = fingerprint(request)
        route_id = new_object_id("rte")
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                ingress = await require_ingress(session, ingress_id)
                await authorize(session, actor, ingress.workspace_id, WorkspaceAction.route_manage)
                replay = await replay_command(
                    session,
                    actor=actor,
                    workspace_id=ingress.workspace_id,
                    operation="route.create",
                    scope_id=ingress_id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                )
                if replay is not None:
                    return (await require_route(session, replay.resource_id)).to_resource()
                adapter = require_adapter(
                    self._adapters, ingress.account.provider_key, ingress.account.provider_config_version
                )
                match, policy = _validate_route(adapter, ingress, request)
                input_mapping = _compile_input_mapping(request.input_mapping)
                await _validate_route_agents(session, ingress, request.agent_id, request.capability_overlays)
                self._validate_batching(
                    request.input_batching.min_interval_ms,
                    request.input_batching.max_batch_events,
                )
                if request.enabled:
                    await require_non_overlapping(session, adapter, ingress_id, match)
                record = RouteRecord(
                    id=route_id,
                    organization_id=ingress.organization_id,
                    workspace_id=ingress.workspace_id,
                    ingress_id=ingress_id,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    provider_config_version=ingress.account.provider_config_version,
                    match_json=match,
                    agent_id=request.agent_id,
                    input_mapping_json=input_mapping,
                    min_interval_ms=request.input_batching.min_interval_ms,
                    max_batch_events=request.input_batching.max_batch_events,
                    capability_overlays_json={
                        key: value.model_dump(mode="json") for key, value in request.capability_overlays.items()
                    },
                    provider_policy_json=policy,
                    enabled=request.enabled,
                    version=1,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                record_command(
                    session,
                    actor=actor,
                    organization_id=ingress.organization_id,
                    workspace_id=ingress.workspace_id,
                    operation="route.create",
                    scope_id=ingress_id,
                    idempotency_key_digest=key_digest,
                    fingerprint=request_fingerprint,
                    resource_type="route",
                    resource_id=route_id,
                    result_version=1,
                    now=now,
                )
                session.add(audit(actor, ingress.organization_id, ingress.workspace_id, "route.create", route_id, now))
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            raise NativeError("route_conflict", "Route identity or name already exists.", status_code=409) from error

    async def list_routes(
        self,
        *,
        actor: AuthenticatedActor,
        ingress_id: str,
        limit: int,
        cursor: str | None,
    ) -> RouteCollection:
        require_limit(limit)
        async with transaction(self._sessions) as session:
            ingress = await require_ingress(session, ingress_id)
            await authorize(session, actor, ingress.workspace_id, WorkspaceAction.route_read)
            scope = {"ingress_id": ingress_id, "actor": actor.principal.model_dump(mode="json")}
            try:
                position = decode_cursor(cursor, scope=scope, id_prefix="rte") if cursor is not None else None
            except CursorError as error:
                raise NativeError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
            query = select(RouteRecord).where(RouteRecord.ingress_id == ingress_id)
            if position is not None:
                query = query.where(
                    or_(
                        RouteRecord.updated_at < position[0],
                        and_(RouteRecord.updated_at == position[0], RouteRecord.id < position[1]),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(RouteRecord.updated_at.desc(), RouteRecord.id.desc()).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = None
            if len(records) > limit:
                last = page[-1]
                next_cursor = encode_cursor(updated_at=last.updated_at, object_id=last.id, scope=scope)
            return RouteCollection(items=tuple(record.to_resource() for record in page), next_cursor=next_cursor)

    async def get_route(self, *, actor: AuthenticatedActor, route_id: str) -> Route:
        async with transaction(self._sessions) as session:
            record = await require_route(session, route_id)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.route_read)
            return record.to_resource()

    async def update_route(
        self,
        *,
        actor: AuthenticatedActor,
        route_id: str,
        request: UpdateRouteRequest,
    ) -> Route:
        async with transaction(self._sessions) as session:
            record = await require_route(session, route_id, lock=True)
            ingress = await require_ingress(session, record.ingress_id)
            await authorize(session, actor, record.workspace_id, WorkspaceAction.route_manage)
            require_version(record.version, request.expected_version)
            adapter = require_adapter(
                self._adapters, ingress.account.provider_key, ingress.account.provider_config_version
            )
            candidate = _updated_route(record, request)
            match, policy = _validate_route(adapter, ingress, candidate)
            input_mapping = _compile_input_mapping(candidate.input_mapping)
            await _validate_route_agents(session, ingress, candidate.agent_id, candidate.capability_overlays)
            self._validate_batching(
                candidate.input_batching.min_interval_ms,
                candidate.input_batching.max_batch_events,
            )
            if candidate.enabled:
                await require_non_overlapping(session, adapter, ingress.id, match, exclude_route_id=record.id)
            record.name = candidate.name
            record.normalized_name = candidate.name.casefold()
            record.match_json = match
            record.agent_id = candidate.agent_id
            record.input_mapping_json = input_mapping
            record.min_interval_ms = candidate.input_batching.min_interval_ms
            record.max_batch_events = candidate.input_batching.max_batch_events
            record.capability_overlays_json = {
                key: value.model_dump(mode="json") for key, value in candidate.capability_overlays.items()
            }
            record.provider_policy_json = policy
            record.enabled = candidate.enabled
            record.version += 1
            record.updated_at = self._clock()
            session.add(
                audit(actor, record.organization_id, record.workspace_id, "route.update", route_id, record.updated_at)
            )
            await session.flush()
            return record.to_resource()

    def _validate_batching(self, min_interval_ms: int, max_batch_events: int) -> None:
        if min_interval_ms > self._batch_max_wait_ms or max_batch_events > self._batch_max_events:
            raise NativeError("invalid_batching_policy", "Route batching exceeds deployment bounds.", status_code=400)


def _updated_route(record: RouteRecord, request: UpdateRouteRequest) -> CreateRouteRequest:
    fields = request.model_fields_set
    return CreateRouteRequest(
        name=request.name if request.name is not None else record.name,
        match=request.match if request.match is not None else record.match_json,
        agent_id=request.agent_id if "agent_id" in fields else record.agent_id,
        input_mapping=request.input_mapping if "input_mapping" in fields else record.input_mapping_json,
        input_batching=request.input_batching
        or InputBatchingPolicy(
            min_interval_ms=record.min_interval_ms,
            max_batch_events=record.max_batch_events,
        ),
        capability_overlays=(
            request.capability_overlays if request.capability_overlays is not None else record.capability_overlays_json
        ),
        provider_policy=(
            request.provider_policy if request.provider_policy is not None else record.provider_policy_json
        ),
        enabled=request.enabled if request.enabled is not None else record.enabled,
    )


def _validate_route(
    adapter: IngressAdapter,
    ingress: IngressRecord,
    request: CreateRouteRequest,
) -> tuple[JsonObject, JsonObject]:
    try:
        return adapter.validate_route(
            match=request.match,
            provider_policy=request.provider_policy,
            account_config=ingress.account.provider_config_json,
            config_version=ingress.account.provider_config_version,
        )
    except ValueError as error:
        raise NativeError(
            "invalid_route_config", "Route provider configuration is invalid.", status_code=400
        ) from error


async def _validate_route_agents(
    session: AsyncSession,
    ingress: IngressRecord,
    agent_id: str | None,
    overlays: Mapping[str, object],
) -> None:
    allowed = frozenset(await ingress_agent_ids(session, ingress.id))
    selected = set(overlays)
    if agent_id is not None:
        selected.add(agent_id)
    if not selected <= allowed:
        raise NativeError("invalid_agent_selection", "Route Agent must be allowed by its Ingress.", status_code=400)


def _compile_input_mapping(value: object | None) -> dict[str, JsonValue] | None:
    if value is None:
        return None
    try:
        return compile_mapping(value).value
    except MappingError as error:
        raise NativeError("invalid_input_mapping", "Route input mapping is invalid.", status_code=400) from error

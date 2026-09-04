"""Shared persistence and validation primitives for Ingress management."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.adapters import IngressAdapter, JsonObject
from a13n_service.connectivity.errors import NativeError

from .models import IngressAgentRecord, IngressRecord, RouteRecord


async def require_ingress(session: AsyncSession, ingress_id: str, *, lock: bool = False) -> IngressRecord:
    query = select(IngressRecord).where(IngressRecord.id == ingress_id)
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise NativeError("resource_not_found", "The requested resource was not found.", status_code=404)
    return record


async def require_route(session: AsyncSession, route_id: str, *, lock: bool = False) -> RouteRecord:
    query = select(RouteRecord).where(RouteRecord.id == route_id)
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise NativeError("resource_not_found", "The requested resource was not found.", status_code=404)
    return record


async def ingress_agent_ids(session: AsyncSession, ingress_id: str) -> tuple[str, ...]:
    return tuple(
        (
            await session.scalars(
                select(IngressAgentRecord.agent_id)
                .where(IngressAgentRecord.ingress_id == ingress_id)
                .order_by(IngressAgentRecord.agent_id)
            )
        ).all()
    )


async def require_routes_fit_agents(session: AsyncSession, ingress_id: str, allowed: frozenset[str]) -> None:
    routes = tuple((await session.scalars(select(RouteRecord).where(RouteRecord.ingress_id == ingress_id))).all())
    for route in routes:
        selected = set(route.capability_overlays_json)
        if route.agent_id is not None:
            selected.add(route.agent_id)
        if not selected <= allowed:
            raise NativeError("invalid_agent_selection", "Existing Routes require removed Agents.", status_code=409)


async def require_non_overlapping(
    session: AsyncSession,
    adapter: IngressAdapter,
    ingress_id: str,
    candidate: JsonObject,
    *,
    exclude_route_id: str | None = None,
) -> None:
    query = select(RouteRecord).where(RouteRecord.ingress_id == ingress_id, RouteRecord.enabled.is_(True))
    if exclude_route_id is not None:
        query = query.where(RouteRecord.id != exclude_route_id)
    for existing in (await session.scalars(query)).all():
        decision = adapter.prove_non_overlap(existing.match_json, candidate)
        if decision is False:
            raise NativeError("route_overlap", "Route match overlaps an enabled Route.", status_code=409)
        if decision is None and not adapter.allows_runtime_ambiguity:
            raise NativeError(
                "route_overlap_unknown",
                "Route match cannot be proven disjoint from an enabled Route.",
                status_code=409,
            )

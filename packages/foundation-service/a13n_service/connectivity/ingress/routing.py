"""Deterministic route and frozen destination resolution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.adapters import IngressAdapter

from .admission_domain import BindingState
from .admission_models import AgentThreadBindingRecord
from .errors import IngressError
from .mapping import CompiledMapping, MappingError, compile_mapping
from .models import IngressRecord, RouteRecord
from .provider import (
    DefaultRoute,
    ExternalRef,
    InboundEvent,
    ProviderEventRouting,
    ProviderIrrelevantEventRouting,
    ProviderRequiresBindingRouting,
)


@dataclass(frozen=True, slots=True)
class IrrelevantRouting:
    kind: Literal["irrelevant"]
    reason_code: str


@dataclass(frozen=True, slots=True)
class RejectedRouting:
    kind: Literal["rejected"]
    reason_code: str
    route: RouteRecord | None = None


@dataclass(frozen=True, slots=True)
class EligibleRouting:
    kind: Literal["eligible"]
    route: RouteRecord | None
    selected_agent_id: str
    external_ref: ExternalRef
    binding_state: BindingState
    binding_id: str | None
    mapping: CompiledMapping
    min_interval_ms: int
    max_batch_events: int
    provider_context: dict[str, JsonValue]
    provider_policy: dict[str, JsonValue]
    native_actions: tuple[str, ...]
    capability_overlay: dict[str, JsonValue] | None
    compatibility_digest: str


type RoutingResolution = IrrelevantRouting | RejectedRouting | EligibleRouting


async def resolve_routing(
    session: AsyncSession,
    *,
    adapter: IngressAdapter,
    ingress: IngressRecord,
    event: InboundEvent,
) -> RoutingResolution:
    if ingress.status != "active":
        return IrrelevantRouting(kind="irrelevant", reason_code="ingress_disabled")
    routes = tuple((await session.scalars(select(RouteRecord).where(RouteRecord.ingress_id == ingress.id))).all())
    try:
        matching = tuple(
            route
            for route in routes
            if adapter.route_matches(event, route.match_json, config_version=ingress.provider_config_version)
        )
    except ValueError as error:
        raise IngressError("invalid_provider_event", "Provider event could not be routed.", status_code=400) from error
    if len(matching) > 1:
        return RejectedRouting(kind="rejected", reason_code="route_ambiguous")
    route = matching[0] if matching else None
    if route is not None and not route.enabled:
        return IrrelevantRouting(kind="irrelevant", reason_code="route_disabled")

    default = _default_route(adapter, ingress, event)
    provider_policy = route.provider_policy_json if route is not None else default.provider_policy
    classification = _classify(adapter, ingress, event, provider_policy)
    if isinstance(classification, ProviderIrrelevantEventRouting):
        return IrrelevantRouting(kind="irrelevant", reason_code=classification.reason_code)
    external_ref = event.refs.get(classification.external_ref_key)
    if external_ref is None:
        return RejectedRouting(kind="rejected", reason_code="correlation_ref_missing", route=route)
    binding = await session.scalar(
        select(AgentThreadBindingRecord).where(
            AgentThreadBindingRecord.ingress_id == ingress.id,
            AgentThreadBindingRecord.external_ref_kind == external_ref.kind,
            AgentThreadBindingRecord.external_ref_id == external_ref.id,
        )
    )
    if isinstance(classification, ProviderRequiresBindingRouting) and binding is None:
        return IrrelevantRouting(kind="irrelevant", reason_code="binding_required")
    selected_agent_id = binding.agent_id if binding is not None else (route.agent_id if route is not None else None)
    if selected_agent_id is None:
        selected_agent_id = ingress.default_agent_id
    mapping_value = (
        route.input_mapping_json
        if route is not None and route.input_mapping_json is not None
        else default.input_mapping
    )
    try:
        mapping = compile_mapping(mapping_value)
    except MappingError as error:
        raise IngressError("invalid_input_mapping", "Frozen input mapping is invalid.", status_code=409) from error
    min_interval_ms = route.min_interval_ms if route is not None else default.input_batching.min_interval_ms
    max_batch_events = route.max_batch_events if route is not None else default.input_batching.max_batch_events
    overlay = None
    if route is not None:
        value = route.capability_overlays_json.get(selected_agent_id)
        overlay = value if isinstance(value, dict) else None
    compatibility = _digest(
        {
            "ingress_id": ingress.id,
            "route_id": route.id if route is not None else None,
            "route_version": route.version if route is not None else None,
            "selected_agent_id": selected_agent_id,
            "external_ref": external_ref.model_dump(mode="json"),
            "binding_id": binding.id if binding is not None else None,
            "mapping_digest": mapping.digest,
            "provider_context": classification.provider_context,
            "provider_policy": provider_policy,
            "native_actions": classification.native_actions,
            "capability_overlay": overlay,
        }
    )
    return EligibleRouting(
        kind="eligible",
        route=route,
        selected_agent_id=selected_agent_id,
        external_ref=external_ref,
        binding_state=BindingState.bound if binding is not None else BindingState.unbound,
        binding_id=binding.id if binding is not None else None,
        mapping=mapping,
        min_interval_ms=min_interval_ms,
        max_batch_events=max_batch_events,
        provider_context=classification.provider_context,
        provider_policy=provider_policy,
        native_actions=classification.native_actions,
        capability_overlay=overlay,
        compatibility_digest=compatibility,
    )


def _default_route(adapter: IngressAdapter, ingress: IngressRecord, event: InboundEvent) -> DefaultRoute:
    try:
        return adapter.default_route(
            event,
            ingress.provider_config_json,
            config_version=ingress.provider_config_version,
        )
    except ValueError as error:
        raise IngressError(
            "invalid_provider_event", "Provider event cannot use default routing.", status_code=400
        ) from error


def _classify(
    adapter: IngressAdapter,
    ingress: IngressRecord,
    event: InboundEvent,
    provider_policy: dict[str, JsonValue],
) -> ProviderEventRouting:
    try:
        return adapter.classify(
            event,
            provider_policy,
            ingress.provider_config_json,
            config_version=ingress.provider_config_version,
        )
    except ValueError as error:
        raise IngressError("invalid_provider_event", "Provider event cannot be classified.", status_code=400) from error


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()

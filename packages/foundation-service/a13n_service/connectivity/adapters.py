"""Stable adapter boundaries used by Connectivity composition."""

from datetime import datetime
from typing import Protocol

from a13n_service.connectivity.ingress.domain import JsonObject
from a13n_service.connectivity.ingress.provider import (
    AdmissionReceipt,
    DefaultRoute,
    InboundEvent,
    ProviderEventRouting,
    ProviderHttpResponse,
    ProviderRequest,
    ProviderRequestDecision,
)


class IngressAdapter(Protocol):
    provider_key: str
    config_versions: frozenset[str]
    allows_runtime_ambiguity: bool
    max_request_bytes: int
    dedup_horizon_seconds: int

    def validate_config(self, value: object, *, config_version: str) -> JsonObject: ...

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> JsonObject: ...

    def configuration_identity(self, value: JsonObject, *, config_version: str) -> object: ...

    def validate_route(
        self,
        *,
        match: object,
        provider_policy: object,
        ingress_config: JsonObject,
        config_version: str,
    ) -> tuple[JsonObject, JsonObject]: ...

    def prove_non_overlap(self, left: JsonObject, right: JsonObject) -> bool | None: ...

    async def authenticate_and_normalize(
        self,
        request: ProviderRequest,
        *,
        ingress_id: str,
        ingress_config: JsonObject,
        credentials: JsonObject,
        received_at: datetime,
    ) -> ProviderRequestDecision: ...

    def route_matches(self, event: InboundEvent, match: JsonObject, *, config_version: str) -> bool: ...

    def default_route(
        self, event: InboundEvent, ingress_config: JsonObject, *, config_version: str
    ) -> DefaultRoute: ...

    def classify(
        self,
        event: InboundEvent,
        provider_policy: JsonObject,
        ingress_config: JsonObject,
        *,
        config_version: str,
    ) -> ProviderEventRouting: ...

    def acknowledge(self, receipt: AdmissionReceipt) -> ProviderHttpResponse: ...

    def failure_response(self, reason_code: str) -> ProviderHttpResponse: ...


class ConnectorAdapter(Protocol):
    driver_key: str
    config_versions: frozenset[str]

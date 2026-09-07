"""Stable adapter boundaries used by Connectivity composition."""

from datetime import datetime
from typing import Protocol

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.ingress.provider import (
    AdmissionReceipt,
    InboundEvent,
    ProviderEventRouting,
    ProviderHttpResponse,
    ProviderRequest,
    ProviderRequestDecision,
    ReceptionDefaults,
)


class AccountAdapter(Protocol):
    provider_key: str
    config_versions: frozenset[str]

    def validate_config(self, value: object, *, config_version: str) -> JsonObject: ...

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> JsonObject: ...

    def configuration_identity(self, value: JsonObject, *, config_version: str) -> object: ...

    def validate_reception_policy(self, value: object, *, config_version: str) -> JsonObject: ...

    def validate_target(self, kind: str, external_id: str) -> str: ...


class IngressAdapter(AccountAdapter, Protocol):
    max_request_bytes: int
    dedup_horizon_seconds: int

    def event_target(self, event: InboundEvent) -> tuple[str, str]: ...

    async def authenticate_and_normalize(
        self,
        request: ProviderRequest,
        *,
        account_id: str,
        account_config: JsonObject,
        credentials: JsonObject,
        received_at: datetime,
    ) -> ProviderRequestDecision: ...

    def reception_defaults(
        self, event: InboundEvent, account_config: JsonObject, *, config_version: str
    ) -> ReceptionDefaults: ...

    def classify(
        self,
        event: InboundEvent,
        provider_policy: JsonObject,
        account_config: JsonObject,
        *,
        config_version: str,
    ) -> ProviderEventRouting: ...

    def acknowledge(self, receipt: AdmissionReceipt) -> ProviderHttpResponse: ...

    def failure_response(self, reason_code: str) -> ProviderHttpResponse: ...

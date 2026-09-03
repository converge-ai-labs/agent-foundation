"""Stable adapter boundaries used by Connectivity composition."""

from typing import Protocol

JsonObject = dict[str, object]


class IngressAdapter(Protocol):
    provider_key: str
    config_versions: frozenset[str]
    allows_runtime_ambiguity: bool

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


class ConnectorAdapter(Protocol):
    driver_key: str
    config_versions: frozenset[str]

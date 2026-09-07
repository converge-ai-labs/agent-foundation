"""Explicit trusted Connector Provider definitions and deterministic validation."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from pydantic import BaseModel

from a13n_service.connectivity.domain import JsonObject

from .contracts import ConnectorProviderRuntime, StrictModel
from .validation import model_json


class ConnectorProviderDefinition(StrictModel):
    type: str
    display_name: str
    configuration_schema: JsonObject
    credential_schema: JsonObject


class ConnectorProviderDefinitionCollection(StrictModel):
    items: tuple[ConnectorProviderDefinition, ...]
    next_cursor: None = None


@dataclass(frozen=True, slots=True)
class ConnectorProviderImplementation:
    type: str
    display_name: str
    configuration_model: type[BaseModel]
    credential_model: type[BaseModel]
    setup_validator: Callable[[JsonObject, str, object], JsonObject]
    factory: Callable[[JsonObject, JsonObject], ConnectorProviderRuntime]

    def definition(self) -> ConnectorProviderDefinition:
        return ConnectorProviderDefinition(
            type=self.type,
            display_name=self.display_name,
            configuration_schema=self.configuration_model.model_json_schema(),
            credential_schema=self.credential_model.model_json_schema(),
        )

    def validate_configuration(self, value: object) -> JsonObject:
        return model_json(self.configuration_model.model_validate(value))

    def validate_credentials(self, value: object) -> JsonObject:
        return model_json(self.credential_model.model_validate(value))

    def validate_setup(self, value: object, *, connector_key: str, configuration: JsonObject) -> JsonObject:
        return self.setup_validator(configuration, connector_key, value)

    def configure(self, configuration: JsonObject, credentials: JsonObject) -> ConnectorProviderRuntime:
        return self.factory(self.validate_configuration(configuration), self.validate_credentials(credentials))


class ConnectorProviderRegistry:
    def __init__(self, implementations: Iterable[ConnectorProviderImplementation] = ()) -> None:
        self._implementations: dict[str, ConnectorProviderImplementation] = {}
        for implementation in implementations:
            self.register(implementation)

    def register(self, implementation: ConnectorProviderImplementation) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", implementation.type) is None:
            raise ValueError("invalid Connector Provider type")
        if implementation.type in self._implementations:
            raise ValueError("duplicate Connector Provider type")
        self._implementations[implementation.type] = implementation

    def require(self, provider_type: str) -> ConnectorProviderImplementation:
        try:
            return self._implementations[provider_type]
        except KeyError as error:
            raise ValueError("Connector Provider type is not registered") from error

    def definitions(self) -> tuple[ConnectorProviderDefinition, ...]:
        return tuple(self._implementations[key].definition() for key in sorted(self._implementations))

    def copy(self) -> ConnectorProviderRegistry:
        return ConnectorProviderRegistry(self._implementations.values())

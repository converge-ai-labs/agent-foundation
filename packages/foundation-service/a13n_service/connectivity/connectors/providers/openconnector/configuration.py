"""OpenConnector implementation-owned configuration and write-only credentials."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ...domain import JsonObject, StrictModel
from ...validation import model_json
from ..configuration import ConnectorKeys, Endpoint


class OpenConnectorConfiguration(StrictModel):
    endpoint: Endpoint = "https://api.openconnector.dev"
    api_profile: Literal["native_v1"] = "native_v1"
    deployment: Literal["cloud", "self_hosted"]
    enabled_provider_slugs: ConnectorKeys

    @model_validator(mode="after")
    def validate_deployment(self) -> OpenConnectorConfiguration:
        official = self.endpoint == "https://api.openconnector.dev"
        if (self.deployment == "cloud") != official:
            raise ValueError("deployment and endpoint do not match")
        return self


class OpenConnectorSetup(StrictModel):
    auth_config_id: str = Field(min_length=1, max_length=256)


def validate_setup(configuration: JsonObject, connector_key: str, value: object) -> JsonObject:
    parsed = OpenConnectorConfiguration.model_validate(configuration)
    if connector_key not in parsed.enabled_provider_slugs:
        raise ValueError("Connector is not enabled")
    return model_json(OpenConnectorSetup.model_validate(value))

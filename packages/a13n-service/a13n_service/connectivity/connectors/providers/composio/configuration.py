"""Composio implementation-owned configuration and write-only credentials."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...domain import JsonObject, StrictModel
from ...validation import model_json
from ..configuration import ConnectorKeys, Endpoint


class ComposioConfiguration(StrictModel):
    endpoint: Endpoint = "https://backend.composio.dev"
    connected_accounts_profile: Literal["v3_1"] = "v3_1"
    tools_profile: Literal["v3_1"] = "v3_1"
    enabled_toolkits: ConnectorKeys
    project_identity: str | None = Field(default=None, min_length=1, max_length=256)


class ComposioSetup(StrictModel):
    auth_config_id: str = Field(min_length=1, max_length=256)
    toolkit_version: str = Field(pattern=r"^[0-9]{8}_[0-9]{2}$")


def validate_setup(configuration: JsonObject, connector_key: str, value: object) -> JsonObject:
    parsed = ComposioConfiguration.model_validate(configuration)
    if connector_key not in parsed.enabled_toolkits:
        raise ValueError("Connector is not enabled")
    return model_json(ComposioSetup.model_validate(value))

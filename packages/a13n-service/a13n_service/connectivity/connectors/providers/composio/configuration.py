"""Composio implementation-owned configuration and write-only credentials."""

from __future__ import annotations

from pydantic import Field

from ...domain import JsonObject, StrictModel
from ...validation import model_json

COMPOSIO_ENDPOINT = "https://backend.composio.dev"


class ComposioConfiguration(StrictModel):
    """The hosted Composio API has no user-configurable settings."""


class ComposioSetup(StrictModel):
    auth_config_id: str = Field(default="managed", min_length=1, max_length=256)
    connection_data: JsonObject = Field(default_factory=dict)
    toolkit_version: str = Field(pattern=r"^[0-9]{8}_[0-9]{2}$")


def validate_setup(configuration: JsonObject, connector_key: str, value: object) -> JsonObject:
    return model_json(ComposioSetup.model_validate(value))

"""OOMOL OpenConnector personal and self-hosted runtime configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from ...domain import StrictModel
from ..configuration import Endpoint

HOSTED_ENDPOINT = "https://connector.oomol.com"


class OpenConnectorConfiguration(StrictModel):
    endpoint: Endpoint = HOSTED_ENDPOINT
    api_profile: Literal["runtime_v1"] = "runtime_v1"
    deployment: Literal["cloud", "self_hosted"] = "cloud"

    @model_validator(mode="after")
    def validate_deployment(self) -> OpenConnectorConfiguration:
        if (self.deployment == "cloud") != (self.endpoint == HOSTED_ENDPOINT):
            raise ValueError("deployment and endpoint do not match")
        return self
